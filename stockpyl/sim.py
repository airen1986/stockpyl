"""Code for simulating multi-echelon inventory systems.

'node' and 'stage' are used interchangeably in the documentation.

The primary data object is the ``SupplyChainNetwork`` and the ``SupplyChainNode`` s
that it contains, which contains all of the data for the simulation instance.

(c) Lawrence V. Snyder
Lehigh University

"""

import numpy as np
import math
from scipy import stats
from tqdm import tqdm				# progress bar
import cProfile

from stockpyl.datatypes import *
from stockpyl.supply_chain_network import *
from stockpyl.supply_chain_node import *
from stockpyl.sim_io import *
from stockpyl.helpers import *
#from tests.instances_ssm_serial import *
from stockpyl.instances import *


# -------------------

# SIMULATION

def simulation(network, num_periods, rand_seed=None, progress_bar=True):
	"""Perform the simulation for ``num_periods`` periods. Fills performance
	measures directly into ``network``.

	Parameters
	----------
	network : SupplyChainNetwork
		The multi-echelon inventory network.
	num_periods : int
		Number of periods to simulate.
	rand_seed : int, optional
		Random number generator seed.
	progress_bar : bool, optional
		Display a progress bar?

	Returns
	-------
	float
		Total cost over all nodes and periods.
	"""

	# TODO: check for directed loops

	# CONSTANTS

	# Number of extra periods to allow for calculations past the last period.
	extra_periods = int(round(np.max([n.order_lead_time or 0 for n in network.nodes]) \
					+ np.max([n.shipment_lead_time or 0 for n in network.nodes]))) + 2

	# INITIALIZATION

	# Initialize state and decision variables at each node.

	# NOTE: State variables are indexed up to num_periods+extra_periods; the
	# additional slots are to allow calculations past the last period.

	for n in network.nodes:

		# Initialize state variable objects for state-variable history list.
		n.state_vars = [NodeStateVars(n, t) for t in range(num_periods+extra_periods)]

	# Initialize random number generator.
	np.random.seed(rand_seed)

	# Initialize progress bar. (If not requested, then this will disable it.)
	pbar = tqdm(total=num_periods, disable=not progress_bar)

	# Initialize state variables.
	initialize_state_vars(network)

	# MAIN LOOP

	for t in range(num_periods):

		# Update period counter for network.
		network.period = t

		# Update progress bar.
		pbar.update()

		# GENERATE DEMANDS AND ORDERS

		# Initialize visited dict.
		visited = {n.index: False for n in network.nodes}

		# Generate demand_list and place orders. Use depth-first search, starting
		# at nodes with no successors, and propagating orders upstream.
		for n in network.source_nodes:
			generate_downstream_orders(n.index, network, t, visited)

		# GENERATE SHIPMENTS

		# Reset visited dict.
		visited = {n.index: False for n in network.nodes}

		# Generate shipments. Use depth-first search, starting at nodes with
		# no predecessors, and propagating shipments downstream.
		for n in network.source_nodes:
			generate_downstream_shipments(n.index, network, t, visited)

		# UPDATE COSTS, PIPELINES, ETC.

		# Set initial values for period t+1 state variables.
		initialize_next_period_state_vars(network, t)

		# Calculate costs.
		calculate_period_costs(network, t)

	# Close progress bar.
	pbar.close()

	# Return total cost.
	return np.sum([n.state_vars[t].total_cost_incurred for n in network.nodes
			for t in range(num_periods)])


# -------------------

# HELPER FUNCTIONS

def generate_downstream_orders(node_index, network, period, visited):
	"""Generate demand_list and orders for all downstream nodes using depth-first-search.
	Ignore nodes for which visited=True.

	Parameters
	----------
	node_index : int
		Index of starting node for depth-first search.
	network : SupplyChainNetwork
		The multi-echelon inventory network.
	period : int
		Time period.
	visited : dict
		Dictionary indicating whether each node in network has already been
		visited by the depth-first search.

	"""
	# Did we already visit this node?
	if visited[node_index]:
		# We shouldn't even be here.
		return

	# Mark node as visited.
	visited[node_index] = True

	# Get the node.
	node = network.get_node_from_index(node_index)

	# Does node have external demand?
	if node.demand_source is not None and node.demand_source.type is not None:
		# Generate demand and fill it in inbound_order_pipeline.
		node.state_vars_current.inbound_order_pipeline[None][0] = \
			node.demand_source.generate_demand(period)

	# Call generate_downstream_orders() for all non-visited successors.
	for s in node.successors():
		if not visited[s.index]:
			generate_downstream_orders(s.index, network, period, visited)

	# Receive inbound orders.
	receive_inbound_orders(node)

	# Get lead times (for convenience).
	order_lead_time = node.order_lead_time or 0
	shipment_lead_time = node.shipment_lead_time or 0

	# Place orders to all predecessors.
	for p in node.predecessors(include_external=True):
		if p is not None:
			# Calculate order quantity.
			order_quantity = node.inventory_policy.get_order_quantity(predecessor_index=p.index)
			# Place order in predecessor's order pipeline.
			# TODO: handle this in a separate function (at the predecessor node)
			p.state_vars_current.inbound_order_pipeline[node_index][order_lead_time] = \
				order_quantity
			p_index = p.index
		else:
			# Calculate order quantity.
			order_quantity = node.inventory_policy.get_order_quantity(predecessor_index=None)
			# Place order to external supplier.
			# (For now, this just means adding to inbound shipment pipeline.)
			# TODO: Handle other types of supply functions
			node.state_vars_current.inbound_shipment_pipeline[None][order_lead_time + shipment_lead_time] = \
				order_quantity
			p_index = None

		# Record order quantity.
		node.state_vars_current.order_quantity[p_index] = order_quantity
		# Add order to on_order_by_predecessor.
		node.state_vars_current.on_order_by_predecessor[p_index] += order_quantity


def generate_downstream_shipments(node_index, network, period, visited):
	"""Generate shipments to all downstream nodes using depth-first-search.
	Ignore nodes for which visited=True.

	Parameters
	----------
	node_index : int
		Index of starting node for depth-first search.
	network : SupplyChainNetwork
		The multi-echelon inventory network.
	period : int
		Time period.
	visited : dict
		Dictionary indicating whether each node in network has already been
		visited by the depth-first search.

	"""
	# Did we already visit this node?
	if visited[node_index]:
		# We shouldn't even be here.
		return

	# Mark node as visited.
	visited[node_index] = True

	# Shortcuts.
	node = network.get_node_from_index(node_index)

	# Remember starting IL.
	starting_inventory_level = node.state_vars_current.inventory_level

	# Receive inbound shipments. (Set inbound_shipment, remove from shipment
	# pipeline, update OO.)
	receive_inbound_shipments(node)

	# Convert raw materials to finished goods.
	new_finished_goods = raw_materials_to_finished_goods(node)

	# Process outbound shipments.
	process_outbound_shipments(node, starting_inventory_level, new_finished_goods)

	# Calculate fill rate (cumulative in periods 0,...,t).
	calculate_fill_rate(node, period)

	# Propagate shipment downstream (i.e., add to successors' inbound_shipment_pipeline).
	propagate_shipment_downstream(node)

	# Call generate_downstream_shipments() for all non-visited successors.
	for s in list(node.successors()):
		if not visited[s.index]:
			generate_downstream_shipments(s.index, network, period, visited)


def initialize_state_vars(network):
	"""Initialize the state variables for each node:

		* inventory_level = to initial_inventory_level
		* inbound_shipment_pipeline = initial_shipments
		* on_order = initial_shipments * shipment_lead_time + initial_orders * order_lead_time
		* inbound_order_pipeline = initial_orders

	Parameters
	----------
	network : SupplyChainNetwork
		The multi-echelon inventory network.
	"""

	# Initialize inventory levels and other quantities.
	for n in network.nodes:
		# Initialize inventory_level to initial_inventory_level (or 0 if None).
		# TODO: handle what happens if initial IL < 0 (or prohibit it)
		n.state_vars[0].inventory_level = n.initial_inventory_level or 0

		# Initialize inbound shipment pipeline and on-order quantities.
		# TODO: allow different initial shipment/order quantities for different pred/succ.
		for p_index in n.predecessor_indices(include_external=True):
			for l in range(n.shipment_lead_time or 0):
				n.state_vars[0].inbound_shipment_pipeline[p_index][l] = n.initial_shipments or 0
			n.state_vars[0].on_order_by_predecessor[p_index] = \
				(n.initial_shipments or 0) * (n.shipment_lead_time or 0) + (n.initial_orders or 0) * (n.order_lead_time or 0)

		# Initialize inbound order pipeline. (Exclude external demand.)
		for s in n.successors():
			for l in range(s.order_lead_time or 0):
				n.state_vars[0].inbound_order_pipeline[s.index][l] = s.initial_orders

		# Initialize raw material inventory.
		# TODO: allow initial RM inventory
		for p in n.predecessor_indices(include_external=True):
			n.state_vars[0].raw_material_inventory[p_index] = 0


def receive_inbound_orders(node):
	"""Receive inbound orders:

		* Set inbound order from pipeline.
		* Remove inbound order from pipeline.
		* Update cumulative demand.

	Parameters
	----------
	node : SupplyChainNode
		The supply chain node.
	"""
	for s_index in node.successor_indices(include_external=True):
		# Set inbound_order from pipeline.
		node.state_vars_current.inbound_order[s_index] = \
			node.state_vars_current.inbound_order_pipeline[s_index][0]
		# Remove order from pipeline.
		node.state_vars_current.inbound_order_pipeline[s_index][0] = 0
		# Update demand_cumul.
		node.state_vars_current.demand_cumul += node.state_vars_current.inbound_order[s_index]


def initialize_next_period_state_vars(network, period):
	"""Set initial values for state variables in period ``period`` + 1.

		* Update shipment and order pipelines by "advancing" them by 1 period \
		and adding a 0 in the last element.
		* Set IL, BO, RM, and OO next period = ending values this period.
		* Set _cumul attributes = ending values this period.

	Parameters
	----------
	network : SupplyChainNetwork
		The multi-echelon inventory network.
	period : int
		The current time period.
	"""

	for n in network.nodes:
		# Update pipelines.
		for p in n.predecessor_indices(include_external=True):
			n.state_vars[period+1].inbound_shipment_pipeline[p] = \
				n.state_vars[period].inbound_shipment_pipeline[p][1:] + [0]
		for s in n.successor_indices(include_external=True):
			n.state_vars[period+1].inbound_order_pipeline[s] = \
				n.state_vars[period].inbound_order_pipeline[s][1:] + [0]

		# Set next period's starting IL, BO, RM, and OO.
		n.state_vars[period+1].inventory_level = n.state_vars[period].inventory_level
		for s_index in n.successor_indices(include_external=True):
			n.state_vars[period+1].backorders_by_successor[s_index] = \
				n.state_vars[period].backorders_by_successor[s_index]
		for p_index in n.predecessor_indices(include_external=True):
			n.state_vars[period+1].on_order_by_predecessor[p_index] = \
				n.state_vars[period].on_order_by_predecessor[p_index]
			n.state_vars[period+1].raw_material_inventory[p_index] = \
				n.state_vars[period].raw_material_inventory[p_index]

		# Set demand_met_from_stock_cumul and demand_cumul.
		n.state_vars[period+1].demand_met_from_stock_cumul = \
			n.state_vars[period].demand_met_from_stock_cumul
		n.state_vars[period+1].demand_cumul = \
			n.state_vars[period].demand_cumul


def calculate_period_costs(network, period):
	"""Calculate costs and revenues for one period.

	Parameters
	----------
	network : SupplyChainNetwork
		The multi-echelon inventory network.
	period : int
		The time period.
	"""

	for n in network.nodes:
		# Finished goods holding cost.
		try:
			n.state_vars[period].holding_cost_incurred = \
				n.local_holding_cost_function(n.state_vars[period].inventory_level)
		except TypeError:
			n.state_vars[period].holding_cost_incurred = \
				(n.local_holding_cost or 0) * max(0, n.state_vars[period].inventory_level)
		# Raw materials holding cost.
		# TODO: Allow different holding costs. Allow holding cost functions.
		# TODO: unit tests
		for p in n.predecessors(include_external=False):
			n.state_vars[period].holding_cost_incurred += \
				(p.local_holding_cost or 0) * n.state_vars[period].raw_material_inventory[p.index]
		# Stockout cost.
		try:
			n.state_vars[period].stockout_cost_incurred = \
				n.stockout_cost_function(n.state_vars[period].inventory_level)
		except TypeError:
			n.state_vars[period].stockout_cost_incurred = \
				(n.stockout_cost or 0) * max(0, -n.state_vars[period].inventory_level)
		# In-transit holding cost.
		if n.in_transit_holding_cost is None:
			h = n.local_holding_cost or 0
		else:
			h = n.in_transit_holding_cost or 0
		n.state_vars[period].in_transit_holding_cost_incurred = \
			h * np.sum([n.state_vars[period].in_transit_to(s) for s in n.successors()])
		# Revenue.
		n.state_vars[period].revenue_earned = (n.revenue or 0) * \
			np.sum([n.state_vars[period].outbound_shipment[s_index] \
					for s_index in n.successor_indices(include_external=True)])

		# Total cost.
		n.state_vars[period].total_cost_incurred = \
			n.state_vars[period].holding_cost_incurred + \
			n.state_vars[period].stockout_cost_incurred + \
			n.state_vars[period].in_transit_holding_cost_incurred - \
			n.state_vars[period].revenue_earned


def receive_inbound_shipments(node):
	"""Receive inbound shipment for the node:

		* Set inbound_shipment.
		* Remove from shipment pipeline and add to raw material inventory.
		* Process as many units as possible.
		* Update IL and OO.

	Parameters
	----------
	node : SupplyChainNode
		The supply chain node.
	"""
	# Loop through predecessors.
	for p_index in node.predecessor_indices(include_external=True):
		# Determine inbound shipment amount from p.
		inbound_shipment = node.state_vars_current.inbound_shipment_pipeline[p_index][0]
		# Set inbound_shipment attribute.
		node.state_vars_current.inbound_shipment[p_index] = inbound_shipment
		# Remove shipment from pipeline.
		node.state_vars_current.inbound_shipment_pipeline[p_index][0] = 0
		# Add shipment to raw material inventory.
		node.state_vars_current.raw_material_inventory[p_index] += inbound_shipment
		# Update on-order inventory.
		node.state_vars_current.on_order_by_predecessor[p_index] -= inbound_shipment


def raw_materials_to_finished_goods(node):
	"""Process raw materials to convert them to finished goods:

		* Remove items from raw material inventory.
		* Update IL.

	Parameters
	----------
	node : SupplyChainNode
		The supply chain node.

	Returns
	-------
	new_finished_goods : float
		Number of new finished goods added to inventory this period.

	"""
	# Determine number of units that can be processed.
	# TODO: handle BOM
	new_finished_goods = np.min([node.state_vars_current.raw_material_inventory[p_index]
						for p_index in node.predecessor_indices(include_external=True)])

	# Process units: remove from raw material inventory and add to finished goods.
	for p_index in node.predecessor_indices(include_external=True):
		node.state_vars_current.raw_material_inventory[p_index] -= new_finished_goods
	node.state_vars_current.inventory_level += new_finished_goods

	return new_finished_goods


def process_outbound_shipments(node, starting_inventory_level, new_finished_goods):
	"""Process outbound shipments for the node:

		* Determine outbound shipments. Demands are satisfied in order of \
		successor node index.
		* Update inventory level.
		* Calculate demand met from stock.

	Parameters
	----------
	node : SupplyChainNode
		The supply chain node.
	starting_inventory_level : float
		Starting inventory level for the period.
	new_finished_goods : float
		Number of new finished goods added to inventory this period.
	"""
	# Determine current on-hand and backorders (after new finished goods are
	# added but before demand is subtracted).
	current_on_hand = max(0.0, starting_inventory_level) + new_finished_goods
	current_backorders = max(0.0, -starting_inventory_level)
	# Double-check BO calculations.
	current_backorders_check = node.get_attribute_total('backorders_by_successor', node.network.period)
	assert np.isclose(current_backorders, current_backorders_check), \
		"current_backorders = {:} <> current_backorders_check = {:}, node = {:d}, period = {:d}".format(
			current_backorders, current_backorders_check, node.index, node.network.period)

	# Determine outbound shipments. (Satisfy demand in order of successor node
	# index.) Also update EIL and BO, and calculate demand met from stock.
	# TODO: allow different allocation policies
	node.state_vars_current.demand_met_from_stock = 0.0
	for s_index in node.successor_indices(include_external=True):
		# Outbound shipment to s = min{OH, BO for s + new order from s}.
		OS = min(current_on_hand, node.state_vars_current.backorders_by_successor[s_index] +
				node.state_vars_current.inbound_order[s_index])
		node.state_vars_current.outbound_shipment[s_index] = OS
		current_on_hand -= OS

		# How much of outbound shipment was used to clear backorders?
		# (Assumes backorders are cleared before satisfying current period's
		# demand_list.)
		BO_OS = min(node.state_vars_current.outbound_shipment[s_index],
					node.state_vars_current.backorders_by_successor[s_index])
		non_BO_OS = node.state_vars_current.outbound_shipment[s_index] - BO_OS

		# Calculate demand met from stock. (Note: This assumes that if there
		# are backorders, they get priority over current period's demand_list.)
		# TODO: handle successor-level DMFS and FR.
		DMFS = max(0, node.state_vars_current.outbound_shipment[s_index]
				- node.state_vars_current.backorders_by_successor[s_index])
		node.state_vars_current.demand_met_from_stock += DMFS
		node.state_vars_current.demand_met_from_stock_cumul += DMFS
		# Update IL and BO.
		node.state_vars_current.inventory_level -= node.state_vars_current.inbound_order[s_index]
		node.state_vars_current.backorders_by_successor[s_index] -= BO_OS

		# Calculate new backorders_by_successor.
		node.state_vars_current.backorders_by_successor[s_index] += max(0,
			node.state_vars_current.inbound_order[s_index] - non_BO_OS)


def calculate_fill_rate(node, period):
	"""Calculate fill rate for the node in the period.

	Parameters
	----------
	node : SupplyChainNode
		The supply chain node.
	period : int
		Time period.

	"""
	# Calculate fill rate (cumulative in periods 0,...,t).
	met_from_stock = node.state_vars[period].demand_met_from_stock_cumul
	total_demand = node.state_vars[period].demand_cumul
	# met_from_stock = np.sum([node.state_vars[t].demand_met_from_stock for t in range(period + 1)])
	# total_demand = np.sum([node.get_attribute_total('inbound_order', t)
	# 					   for t in range(period + 1)])

	if total_demand > 0:
		node.state_vars_current.fill_rate = met_from_stock / total_demand
	else:
		node.state_vars_current.fill_rate = 1.0


def propagate_shipment_downstream(node):
	"""Propagate shipment downstream, i.e., add it to successors' ``inbound_shipment_pipeline``.

	Parameters
	----------
	node : SupplyChainNode
		The supply chain node.

	Returns
	-------
	inbound_shipment : float
		The inbound shipment quantity.

	"""
	# Propagate shipment downstream (i.e., add to successors' inbound_shipment_pipeline).
	# TODO: handle end of horizon -- if period+s.shipment_lead_time > T
	for s in node.successors():
		s.state_vars_current.inbound_shipment_pipeline[node.index][s.shipment_lead_time or 0] \
			= node.state_vars_current.outbound_shipment[s.index]


# -------------------

# SIMULATION STUFF

def run_multiple_trials(network, num_trials, num_periods, rand_seed=None, progress_bar=True):
	"""Run ``num_trials`` trials of the simulation, each with  ``num_periods``
	periods. Return mean and SEM of average cost.

	(To build alpha-confidence interval, use
	``mean_cost`` +/- z_{1-alpha/2} * ``sem_cost``.)

	Note: After trials, ``network`` will contain performance measures for the
	most recent trial.

	TODO: figure out how to handle randseed -- need to avoid setting it in simulation()

	Parameters
	----------
	network : SupplyChainNetwork
		The multi-echelon inventory network.
	num_trials : int
		Number of trials to simulate.
	num_periods : int
		Number of periods to simulate.
	rand_seed : int, optional
		Random number generator seed.
	progress_bar : bool, optional
		Display a progress bar?

	Returns
	-------
	mean_cost : float
		Mean of average costs across all trials.
	sem_cost : float
		Standard error of average costs across all trials.
	"""

	# Initialize list of average costs.
	average_costs = []

	# Initialize progress bar. (If not requested, then this will disable it.)
	pbar = tqdm(total=num_trials, disable=not progress_bar)

	# Initialize random number generator seed. The idea for now is to initialize
	# it with rand_seed (which is possibly None); then, for each trial, initialize it by generating a
	# randint. This is because calling np.random.seed(None) is very slow
	# (it was the bottleneck of the simulation when running multiple trials)
	# so I'm generating seeds pseudo-randomly. Not sure this is the best approach.
	np.random.seed(rand_seed)

	# Run trials.
	for t in range(num_trials):
		# Update progress bar.
		pbar.update()

		total_cost = simulation(network, num_periods, rand_seed=np.random.randint(1, 10000), progress_bar=False)
		average_costs.append(total_cost / num_periods)

	# Close progress bar.
	pbar.close()

	# Calculate mean and SEM of average cost.
	mean_cost = np.mean(average_costs)
	sem_cost = stats.sem(average_costs, ddof=0)

	return mean_cost, sem_cost

