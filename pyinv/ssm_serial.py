"""Code to implement Chen-Zheng (1994) algorithm for stochastic serial systems
under stochastic service model (SSM), as described in Snyder and Shen (2019).

'node' and 'stage' are used interchangeably in the documentation.

The primary data object is the ``SupplyChainNetwork`` and the ``SupplyChainNode``s
that it contains, which contains all of the data for the SSM instance.

The following parameters are used to specify input data:
	* Node-level parameters
		- echelon_holding_cost [h]
		- stockout_cost [p]
		- lead_time [L]
		- demand_mean [mu]
		- demand_standard_deviation [sigma]
	* Edge-level parameters
		(None.)

The following attributes are used to store outputs and intermediate values:
	* Graph-level parameters
		(None.)
	* Node-level parameters
		(None.)

(c) Lawrence V. Snyder
Lehigh University and Opex Analytics

"""

import numpy as np
from scipy import stats
import math
import matplotlib.pyplot as plt
import copy

from pyinv.helpers import *


### NETWORK-HANDLING FUNCTIONS ###

def local_to_echelon_base_stock_levels(network, S_local):
	"""Convert local base-stock levels to echelon base-stock levels.

	Assumes network is serial system but does not assume anything about the
	labeling of the nodes.

	Parameters
	----------
	network : SupplyChainNetwork
		The serial inventory network.
	S_local : dict
		Dict of local base-stock levels.

	Returns
	-------
	S_echelon : dict
		Dict of echelon base-stock levels.

	"""
	S_echelon = {}
	for n in network.nodes:
		S_echelon[n.index] = S_local[n.index]
		k = n.get_one_successor()
		while k is not None:
			S_echelon[n.index] += S_local[k.index]
			k = k.get_one_successor()

	return S_echelon


def echelon_to_local_base_stock_levels(network, S_echelon):
	"""Convert echelon base-stock levels to local base-stock levels.

	Assumes network is serial system but does not assume anything about the
	labeling of the nodes.

	Parameters
	----------
	network : SupplyChainNetwork
		The serial inventory network.
	S_echelon : dict
		Dict of echelon base-stock levels.

	Returns
	-------
	S_local : dict
		Dict of local base-stock levels.

	"""
	S_local = {}

	# Determine indexing of nodes. (node_list[i] = index of i'th node, where
	# i = 0 means sink node and i = N-1 means source node.)
	node_list = []
	n = network.sink_nodes[0]
	while n is not None:
		node_list.append(n.index)
		n = n.get_one_predecessor()

	# Calculate S-minus.
	S_minus = {}
	j = 0
	for n in network.nodes:
		S_minus[n.index] = np.min([S_echelon[node_list[i]]
							 for i in range(j, len(S_echelon))])
		j += 1

	# Calculate S_local.
	for n in network.nodes:
		# Get successor.
		k = n.get_one_successor()
		if k is None:
			S_local[n.index] = S_minus[n.index]
		else:
			S_local[n.index] = S_minus[n.index] - S_minus[k.index]

	return S_local


### COST-RELATED FUNCTIONS ###

def expected_cost(network, echelon_S, x_num=1000, d_num=100,
				  ltd_lower_tail_prob=1-stats.norm.cdf(4),
				  ltd_upper_tail_prob=1-stats.norm.cdf(4),
				  sum_ltd_lower_tail_prob=1-stats.norm.cdf(4),
				  sum_ltd_upper_tail_prob=1-stats.norm.cdf(8)):
	"""Calculate expected cost of given solution.

	This is a wrapper function that calls ``optimize_base_stock_levels()``
	without doing any optimization.

	Parameters
	----------
	network : SupplyChainNetwork
		The serial inventory network.
	echelon_S : dict
		Dict of echelon base-stock levels to be evaluated.
	x_num : int, optional
		Number of discretization intervals to use for ``x`` range.
	d_num : int, optional
		Number of discretization intervals to use for ``d`` range.
	ltd_lower_tail_prob : float, optional
		Lower tail probability to use when truncating lead-time demand
		distribution.
	ltd_upper_tail_prob : float, optional
		Upper tail probability to use when truncating lead-time demand
		distribution.
	sum_ltd_lower_tail_prob : float, optional
		Lower tail probability to use when truncating "sum-of-lead-times"
		demand distribution.
	sum_ltd_upper_tail_prob : float, optional
		Upper tail probability to use when truncating "sum-of-lead-times"
		demand distribution.

	Returns
	-------
	cost : float
		Expected cost of system.
	"""
	_, cost = optimize_base_stock_levels(network, S=echelon_S, plots=False,
										 x=None, x_num=x_num, d_num=d_num,
										 ltd_lower_tail_prob=ltd_lower_tail_prob,
										 ltd_upper_tail_prob=ltd_upper_tail_prob,
										 sum_ltd_lower_tail_prob=sum_ltd_lower_tail_prob,
										 sum_ltd_upper_tail_prob=sum_ltd_upper_tail_prob)

	return cost


def expected_holding_cost(network, echelon_S, x_num=1000, d_num=100,
						  ltd_lower_tail_prob=1-stats.norm.cdf(4),
						  ltd_upper_tail_prob=1-stats.norm.cdf(4),
						  sum_ltd_lower_tail_prob=1-stats.norm.cdf(4),
						  sum_ltd_upper_tail_prob=1-stats.norm.cdf(8)):
	"""Calculate expected holding cost of given solution.

	Basic idea: set stockout cost to 0 and call ``optimize_base_stock_levels()``
	without doing any optimization.

	Parameters
	----------
	network : SupplyChainNetwork
		The serial inventory network.
	echelon_S : dict
		Dict of echelon base-stock levels to be evaluated.
	x_num : int, optional
		Number of discretization intervals to use for ``x`` range.
	d_num : int, optional
		Number of discretization intervals to use for ``d`` range.
	ltd_lower_tail_prob : float, optional
		Lower tail probability to use when truncating lead-time demand
		distribution.
	ltd_upper_tail_prob : float, optional
		Upper tail probability to use when truncating lead-time demand
		distribution.
	sum_ltd_lower_tail_prob : float, optional
		Lower tail probability to use when truncating "sum-of-lead-times"
		demand distribution.
	sum_ltd_upper_tail_prob : float, optional
		Upper tail probability to use when truncating "sum-of-lead-times"
		demand distribution.

	Returns
	-------
	holding_cost : float
		Expected holding cost of system.
	"""

	# Make copy of network and set stockout cost to 0.
	network2 = copy.deepcopy(network)
	for node in network2.nodes:
		node.stockout_cost = 0

	_, holding_cost = optimize_base_stock_levels(network2, S=echelon_S,
								plots=False, x=None, x_num=x_num, d_num=d_num,
								ltd_lower_tail_prob=ltd_lower_tail_prob,
								ltd_upper_tail_prob=ltd_upper_tail_prob,
								sum_ltd_lower_tail_prob=sum_ltd_lower_tail_prob,
								sum_ltd_upper_tail_prob=sum_ltd_upper_tail_prob)

	return holding_cost


### OPTIMIZATION ###

def optimize_base_stock_levels(network, S=None, plots=False, x=None,
							   x_num=1000, d_num=100,
							   ltd_lower_tail_prob=1-stats.norm.cdf(4),
							   ltd_upper_tail_prob=1-stats.norm.cdf(4),
							   sum_ltd_lower_tail_prob=1-stats.norm.cdf(4),
							   sum_ltd_upper_tail_prob=1-stats.norm.cdf(8)):
	"""Chen-Zheng (1994) algorithm for stochastic serial systems under
	stochastic service model (SSM), as described in Snyder and Shen (2019).

	Stages are assumed to be indexed N, ..., 1.

	Parameters
	----------
	network : SupplyChainNetwork
		The serial inventory network.
	S : dict, optional
		Dict of echelon base-stock levels to evaluate. If present, no
		optimization is performed and the function just returns the cost
		under base-stock levels ``S``; to optimize instead, set ``S``=``None``
		(the default).
	plots : bool
		``True`` for the function to generate plots of C(.) functions,
		``False`` o/w.
	x : ndarray, optional
		x-array to use for truncation and discretization, or ``None`` (the default)
		to determine automatically.
	x_num : int, optional
		Number of discretization intervals to use for ``x`` range. Ignored if
		``x`` is provided.
	d_num : int, optional
		Number of discretization intervals to use for ``d`` range.
	ltd_lower_tail_prob : float, optional
		Lower tail probability to use when truncating lead-time demand
		distribution.
	ltd_upper_tail_prob : float, optional
		Upper tail probability to use when truncating lead-time demand
		distribution.
	sum_ltd_lower_tail_prob : float, optional
		Lower tail probability to use when truncating "sum-of-lead-times"
		demand distribution.
	sum_ltd_upper_tail_prob : float, optional
		Upper tail probability to use when truncating "sum-of-lead-times"
		demand distribution.

	Returns
	-------
	S_star : dict
		Dict of optimal echelon base-stock levels.
	C_star : float
		Optimal expected cost.
	"""

	# Get number of nodes.
	N = len(network.nodes)

	# Make sure nodes are indexed correctly.
	assert network.source_nodes[0].index == N and network.sink_nodes[0].index == 1, \
		"nodes must be indexed N, ..., 1"

	# TODO: Make sure echelon holding costs are provided.

	# Get shortcuts to parameters (for convenience).
	sink = network.get_node_from_index(1)
	mu = sink.demand_source.demand_distribution().mean()
	L = np.zeros(N+1)
	h = np.zeros(N+1)
	for j in network.nodes:
		L[j.index] = j.lead_time
		h[j.index] = j.echelon_holding_cost
	p = sink.stockout_cost

	# Get "sum of lead-time demand" distribution (LTD distribution in which
	# L = sum of all lead times).
	sum_ltd_dist = sink.demand_source.lead_time_demand_distribution(np.sum(L))

	# TODO: would it be better to calculate mean, a, b directly if we know them?

	# Get truncation bounds for sum-of-lead-time demand distribution.
	# If support is finite, use support; otherwise, use F^{-1}(.).
	if sum_ltd_dist.a == float("-inf"):
		sum_ltd_lo = sum_ltd_dist.ppf(sum_ltd_lower_tail_prob)
	else:
		# interval(1) gives lo and hi end of interval that contains 100% of
		# probability area, i.e., 0th and 100th percentile, which for uniform
		# distribution is the entire support.
		sum_ltd_lo = sum_ltd_dist.interval(1)[0]
	if sum_ltd_dist.b == float("inf"):
		sum_ltd_hi = sum_ltd_dist.ppf(1 - sum_ltd_upper_tail_prob)
	else:
		sum_ltd_hi = sum_ltd_dist.interval(1)[1]

	# Determine x (inventory level) array (truncated and discretized).
	if x is None:
		# TODO: is this the best x-range to use?
		# x-range = [sum_ltd_lo-sum_ltd_mean, sum_ltd_hi].
		x_lo = sum_ltd_lo - sum_ltd_dist.mean()
		x_hi = sum_ltd_hi
		x_delta = (x_hi - x_lo) / x_num
		# x_lo = -4 * sigma * np.sqrt(sum(L))
		# x_hi = mu * sum(L) + 8 * sigma * np.sqrt(sum(L))
		# x_delta = (x_hi - x_lo) / x_num
		# Ensure x >= largest echelon BS level, if provided.
		if S is not None:
			x_hi = max(x_hi, max(S, key=S.get))
		# Build x range.
		x = np.arange(x_lo, x_hi + x_delta, x_delta)
	else:
		x_delta = x[1] - x[0]

	# Standard normal demand array (truncated and discretized).
	# (Use mu + d * sigma to get distribution-specific demands).
	# d_lo = -4
	# d_hi = 4
	# d_delta = (d_hi - d_lo) / d_num
	# d = np.arange(d_lo, d_hi + d_delta, d_delta)
	# # Probability array.
	# fd = stats.norm.cdf(d+d_delta/2) - stats.norm.cdf(d-d_delta/2)

	# Extended x array (used for approximate C-hat function).
	x_ext_lo = np.min(x) - sum_ltd_hi
	x_ext_hi = np.max(x) + sum_ltd_hi
	# x_ext_lo = np.min(x) - (mu * sum(L) +
	# 						d.max() * sigma * np.sqrt(sum(L)))
	# x_ext_hi = np.max(x) + (mu * sum(L) +
	# 						d.max() * sigma * np.sqrt(sum(L)))
	x_ext = np.arange(x_ext_lo, x_ext_hi, x_delta)

	# Initialize arrays. (0th element is ignored since we are assuming stages
	# are numbered starting at 1. C_bar is an exception since C_bar[0] is
	# meaningful.)
	C_hat = np.zeros((N+1, len(x)))
	C = np.zeros((N+1, len(x)))
	S_star = {}
	C_star = np.zeros(N+1)
	C_bar = np.zeros((N+1, len(x)))

	# Initialize arrays containing approximation for C (mainly for debugging).
	C_hat_lim1 = np.zeros((N+1, len(x_ext)))
	C_hat_lim2 = np.zeros((N+1, len(x_ext)))

	# Calculate C_bar[0, :].
	C_bar[0, :] = (p + sum(h)) * np.maximum(-x, 0)

	# Loop through stages.
	for j in range(1, N+1):

#		print(str(j))

		# Calculate C_hat.
		C_hat[j, :] = h[j] * x + C_bar[j-1, :]

		# Calculate approximate C-hat function.
		C_hat_lim1[j, :] = -(p + sum(h)) * (x_ext - mu * sum(L[1:j]))
		for i in range(1, j+1):
			C_hat_lim1[j, :] += h[i] * (x_ext - mu * sum(L[i:j]))
		# C_hat_lim2 is never used since y-d is never greater than the y range;
		# however, it is here so it can be plotted.
		if j > 1:
			C_hat_lim2[j, :] = h[j] * x_ext + C[j-1, find_nearest(x, S_star[j-1], True)]
		else:
			C_hat_lim2[j, :] = h[j] * x_ext

		# Get lead-time demand distribution.
		# TODO: what happens if demand_source does not provide these functions?
		ltd_dist = sink.demand_source.lead_time_demand_distribution(L[j])

		# Get truncation bounds for lead-time demand distribution.
		# If support is finite, use support; otherwise, use F^{-1}(.).
		if ltd_dist.a == float("-inf"):
			ltd_lo = ltd_dist.ppf(ltd_lower_tail_prob)
		else:
			ltd_lo = ltd_dist.interval(1)[0]
		if ltd_dist.b == float("inf"):
			ltd_hi = ltd_dist.ppf(1 - ltd_upper_tail_prob)
		else:
			ltd_hi = ltd_dist.interval(1)[1]

		# Determine d (lead-time demand) array (truncated and discretized).
		d_lo = ltd_lo
		d_hi = ltd_hi
		d_delta = (d_hi - d_lo) / d_num
		d = np.arange(d_lo, d_hi + d_delta, d_delta)

		# Calculate discretized cdf array.
		# TODO: handle situation where demand_distrib does not provide _cdf
		fd = np.array([ltd_dist.cdf(d_val+d_delta/2) - ltd_dist.cdf(d_val-d_delta/2) for d_val in d])
#		fd = ltd_dist.cdf(d+d_delta/2) - ltd_dist.cdf(d-d_delta/2)

		# Calculate C.
		for y in x:

			# Get index of closest element of x to y.
			the_x = find_nearest(x, y, True)

			# Loop through demands and calculate expected cost.
			# This method uses the following result (see Problem X.X):
			# TODO: supply problem number
			# lim_{y -> -infty} C_j(y) = -(p + h'_{j+1})(y - sum_{i=1}^j E[D_i])
			# lim_{y -> +infty} C_j(y) = h_j(y - E[D_j]) + C_{j-1}(S*_{j-1})
			# TODO: this can probably be vectorized and be much faster

			the_cost = np.zeros(d.size)

			for d_ind in range(d.size):
				# Calculate y - d.
				y_minus_d = y - d[d_ind]
#				y_minus_d = y - (mu * L[j] + d[d_ind] * sigma * np.sqrt(L[j]))

				# Calculate cost -- use approximate value of C-hat if y-d is
				# outside of x-range.
				if y_minus_d < np.min(x):
					the_cost[d_ind] = \
						C_hat_lim1[j, find_nearest(x_ext, y_minus_d, True)]
				elif y_minus_d > np.max(x): # THIS SHOULD NEVER HAPPEN
					the_cost[d_ind] = \
						C_hat_lim2[j, find_nearest(x_ext, y_minus_d, True)]
				else:
					the_cost[d_ind] = \
						C_hat[j, find_nearest(x, y_minus_d, True)]

			# Calculate expected cost.
			C[j, the_x] = np.dot(fd, the_cost)

		# Did user specify S?
		if S is None:
			# No -- determine S*.
			opt_S_index = np.argmin(C[j, :])
			S_star[j] = x[opt_S_index]
		else:
			# Yes -- use specified S.
			S_star[j] = S[j]
		C_star[j] = C[j, find_nearest(x, S_star[j], True)]

		# Calculate C_bar
		C_bar[j, :] = C[j, find_nearest(x, np.minimum(S_star[j], x), True)]

	# Plot functions.
	if plots:
		fig, axes = plt.subplots(2, 2)
		# C_hat.
		axes[0, 0].plot(x, np.transpose(C_hat[1:N+1, :]))
		axes[0, 0].set_title('C-hat')
		axes[0, 0].legend(list(map(str, range(1, N+1))))
		k = N
		axes[0, 0].plot(x, C_hat_lim1[k, find_nearest(x_ext, x)], ':')
		axes[0, 0].plot(x, C_hat_lim2[k, find_nearest(x_ext, x)], ':')
		# C_bar.
		axes[0, 1].plot(x, np.transpose(C_bar))
		axes[0, 1].set_title('C-bar')
		axes[0, 1].legend(list(map(str, range(N+1))))
		# C.
		axes[1, 0].plot(x, np.transpose(C[1:N+1, :]))
		axes[1, 0].set_title('C')
		axes[1, 0].legend(list(map(str, range(1, N+1))))

		plt.show()

	return S_star, C_star[N]


#S_star, C_star = optimize_base_stock_levels(instance_2_stage, plots=False)
#print('S_star = {}, C_star = {}'.format(S_star, C_star))
