import random
from .representation import Tree, Node
from ..generate import BodyGenerator, NeuralNetworkGenerator
from ..spec.msgs import BodyPart


def decide(probability):
    """
    Returns True with the given probability
    :param probability:
    :return:
    """
    return random.random() < probability


def _node_list(node, root=True):
    """
    Recursively builds a linear node list
    from a root node.

    :param node:
    :type node: Node
    :param root: Include the given node or not
    :return:
    """
    if root:
        lst = [node]
    else:
        lst = []

    for conn in node.child_connections():
        lst += _node_list(conn.node, root=True)

    return lst


def _renumber(node, base=0):
    """
    :param base:
    :param node:
    :return:
    """
    for node in _node_list(node):
        node.id = "node-%d" % base
        base += 1

    return base


class Crossover(object):
    """
    Crossover class. Its working part is the `crossover` function, see
    there for a detailed description.
    """
    def __init__(self, body_gen, brain_gen):
        """
        Although the crossover class does not perform parameter mutations, robot
        limits are still required knowledge for crossover choices. It is because
        of this the body / brain generators are passed here.

        :param body_gen: A body generator for this robot spec.
        :type body_gen: BodyGenerator
        :param brain_gen: A neural net generator for this robot spec.
        :type brain_gen: NeuralNetworkGenerator
        :return:
        """
        self.brain_gen = brain_gen
        self.body_gen = body_gen

    def crossover(self, a, b):
        """
        Performs actual crossover between two robot trees, a and b. This
        works as follows:

        - Robot `a` is copied
        - A random node `q` is picked from this copied robot. This may
          be anything but the root node.
        - We generate a list of nodes from robot b which, when replacing `q`,
          would not violate the robot's specifications. If this list is empty,
          crossover is not performed.
        - We pick a random node `r` from this list
        :param a:
        :type a: Tree
        :param b:
        :type b: Tree
        :return:
        :rtype: Tree
        """
        result = a.root.copy()

        # All nodes except the root node
        crossover_points = _node_list(result)[1:]
        q = random.choice(crossover_points)

        # Create list of valid crossover points from `b`
        # Get the total list of nodes excluding the root
        b_nodes = _node_list(b.root, root=False)

        # Determine the current robot statistics, subtracting
        # everything after the crossover point.
        inputs, outputs, hidden = result.io_count()
        num_nodes = len(result)

        c_i, c_o, c_h = q.io_count()
        inputs -= c_i
        outputs -= c_o
        hidden -= c_h
        num_nodes -= len(q)

        replace_nodes = []
        max_parts = self.body_gen.max_parts
        max_inputs = self.body_gen.max_inputs
        max_outputs = self.body_gen.max_outputs
        max_hidden = self.brain_gen.max_hidden

        for node in b_nodes:
            n_nodes = len(node)
            if num_nodes + n_nodes > max_parts:
                # Using this node would result in too many parts
                continue

            n_i, n_o, n_h = node.io_count()
            t_i, t_o, t_h = n_i + inputs, n_o + outputs, n_h + hidden
            if t_i > max_inputs or t_o > max_outputs or t_h > max_hidden:
                continue

            replace_nodes.append(node)

        if not replace_nodes:
            # No possible replacement nodes - no crossover
            return False, Tree(result)

        # Pick `r` from list.
        r = random.choice(replace_nodes)

        # Replace the subtree starting at `q` with `r`
        # Determine the connection slots. Slots are always relative to
        # the node defining the connection. We thus need the `to_slot`
        # of the parent connection of the original node, and connect it
        # to the `from_slot` on the new node.
        conn = q.parent_connection()
        start_node = conn.node
        from_slot = conn.to_slot
        to_slot = r.parent_connection().from_slot

        # Remove the existing connection. `set_connection` will also do this,
        # but this way we make sure the trees never contain duplicate IDs
        # and that seems desireable somehow.
        start_node.remove_connection(from_slot)

        # We don't want to modify `b`, so copy `r` without parent
        r = r.copy(copy_parent=False)

        # Update node IDs, duplicates are quite likely at this point
        base = _renumber(result)
        _renumber(r, base)

        # Set the actual connection and return the tree
        result.set_connection(from_slot, to_slot, r, parent=True)

        return True, Tree(result)


def _delete_subtree(node):
    """
    Removes the subtree starting from the given node
    :param node:
    :type node: Node
    :return:
    """
    conn = node.parent_connection()
    node.remove_connection(conn.from_slot)


class Mutator(object):
    """
    Parameter mutation class. Mutation is achieved by generating
    a new value for each parameter and combining it with the
    old parameter in the ratio specified by the parameter's
    epsilon value.
    """

    def __init__(self, body_gen, brain_gen,
                 p_delete_hidden_neuron=0.05,
                 p_remove_brain_connection=0.05,
                 p_delete_subtree=0.05,
                 p_swap_subtree=0.05,
                 p_duplicate_subtree=0.05):
        """
        :param body_gen: A body generator for this robot.
        :type body_gen: BodyGenerator
        :param brain_gen: A neural net generator for this robot
        :type brain_gen: NeuralNetworkGenerator
        :return:
        """
        self.p_duplicate_subtree = p_duplicate_subtree
        self.p_swap_subtree = p_swap_subtree
        self.p_delete_subtree = p_delete_subtree
        self.p_delete_brain_connection = p_remove_brain_connection
        self.p_delete_hidden_neuron = p_delete_hidden_neuron
        self.brain_gen = brain_gen
        self.body_gen = body_gen

    def mutate(self, tree, in_place=True):
        """
        Mutates the robot tree. This performs the following operations:

        - Body parameters are mutated
        - Brain parameters are mutated
        - A subtree might be removed
        - A subtree might be duplicated
        - Two subtrees might be swapped
        - Subtrees are duplicated at random
        - Body parts are added at random

        Mutation operations are designed to make changes to the robot
        whilst keeping it at roughly the same complexity. This means that:

        - The probability of a new body part being added is proportional
          to the average number of body parts being removed in a single
          step.
        - The number of newly created hidden neurons and neural connections
          equals the average number of neurons and connections removed in
          each step.

        :param tree:
        :type tree: Tree
        :param in_place:
        :return:
        """
        root = tree.root if in_place else tree.root.copy()
        nodes = _node_list(root, root=False)
        initial_length = len(nodes) + 1
        subtrees = [len(node) for node in nodes]

        # First, we delete a random subtree (this might make some space)
        deleted, avg_del_len = self.delete_random_subtree(root)

        # Next, we duplicate a random subtree
        duplicated, avg_dup_len = self.duplicate_random_subtree(root)

        # We then swap two random subtrees
        self.swap_random_subtrees(root)

        hidden_before = 0
        hidden_after = 0
        conn_before = 0
        conn_after = 0
        node_list = _node_list(root, root=True)
        for node in node_list:
            # Delete hidden neurons at random
            hidden_before += len(node.neurons)
            node.neurons[:] = [neuron for neuron in node.neurons
                               if neuron.type != "hidden" or decide(1.0 - self.p_delete_hidden_neuron)]
            hidden_after += len(node.neurons)

            # Delete brain connections at random
            conn_before += len(node.neural_connections)
            node.neural_connections[:] = [conn for conn in node.neural_connections
                                          if decide(1.0 - self.p_delete_brain_connection)]
            conn_after += len(node.neural_connections)

            # Mutate body and brain parameters
            self.mutate_node_body_parameters(node)
            self.mutate_node_brain_parameters(node)

        # Next, we perform additive changes to the body
        # First, we add a body part at random. To roughly maintain
        # robot complexity, the probability of doing this is proportional
        # to the average number of body parts that have been previously
        # removed, minus the ones that have been added by duplication.
        p_add_body_part = avg_dup_len * self.p_duplicate_subtree - avg_del_len * self.p_delete_subtree
        added = self.add_random_body_part(p_add_body_part, root)

        # We then add new hidden neurons. We don't want to bias
        # the number of hidden neurons through this procedure,
        # so we want to add, on average, as many as we remove -
        # though we don't want it to remain strictly the same.
        # We thus take the number of hidden neurons before
        # they were removed and multiply it by the deletion
        # probability, and correct for the nodes that may
        # have been added.
        # TODO Select a new number of hidden neurons and add them

        # Finally, we add new neural connections
        # TODO Add new neural net connections

        # Renumber the entire tree
        _renumber(root)

    def delete_random_subtree(self, root):
        """
        Deletes a subtree at random, assuming this is possible within
        the boundaries of the robot specification.
        :param root: Root node of the tree
        :return: The removed subtree (or None if no subtree was removed)
        :rtype: Node
        """
        node_list = _node_list(root, root=False)
        max_remove_size = len(node_list) + 1 - self.body_gen.min_parts
        items = [node for node in node_list if len(node) <= max_remove_size]
        avg_del_len = sum(len(node) for node in items) / float(len(items))
        if not items or not decide(self.p_delete_subtree):
            return None, avg_del_len

        subtree = random.choice(items)
        _delete_subtree(subtree)
        return subtree, avg_del_len

    def duplicate_random_subtree(self, root):
        """
        Picks a random subtree that can be duplicated within the robot
        boundaries, copies it and attaches it to a random free slot.
        :param root:
        :type root: Node
        :return:
        """
        node_list = _node_list(root, root=False)
        inputs, outputs, hidden = root.io_count()
        max_add_size = len(node_list) + 1 - self.body_gen.max_parts

        # Create a list of subtrees that
        # - Is not larger than max_add_size
        # - Does not violate I/O constraints when added
        mi, mo, mh = self.body_gen.max_inputs, self.body_gen.max_outputs, self.brain_gen.max_hidden

        def valid_part(node):
            """
            :type node: Node
            """
            if len(node) > max_add_size:
                return False

            i, o, h = node.io_count()
            return (i + inputs) <= mi and (o + outputs) <= mo and (h + hidden) <= mh

        # If there are no valid nodes or attachment positions, duplication will
        # never happen and the average duplication length is 0. Deciding this
        # in parts cuts the calculation short making it faster.
        nodes = [node for node in node_list if valid_part(node)]
        if not nodes:
            return None, 0

        # Generate a list of attachment points
        node_list.append(root)
        attachments = [(node, slot) for node in node_list
                       for slot in node.get_free_slots()]

        if not attachments:
            return None, 0

        # Only duplicate with the given probability
        avg_dup_len = sum(len(node) for node in nodes)
        if not decide(self.p_duplicate_subtree):
            return None, avg_dup_len

        # Pick a random node to duplicate
        dup = random.choice(nodes)
        """ :type : Node """

        attach_node, attach_slot = self.body_gen.choose_attachment(attachments)
        dup_new = dup.copy(copy_parent=False)
        dup_new.set_connection(dup.parent_connection().from_slot, attach_slot, attach_node, parent=False)
        return dup_new, avg_dup_len

    def swap_random_subtrees(self, root):
        """
        Picks to random subtrees (which are not parents / children of each other)
        and swaps them.
        :param root:
        :return: The two body parts on which swapping was performed, or (None, None)
                 if this did not happen.
        """
        if not decide(self.p_swap_subtree):
            return None, None

        nodes = _node_list(root, root=False)
        if not nodes:
            return None, None

        a = random.choice(nodes)
        """ :type : Node """

        related = set([a] + a.get_parents() + a.get_children())
        swaps = [node for node in nodes if node not in related]
        if not swaps:
            return None, None

        b = random.choice(swaps)
        """ :type : Node """

        a_conn = a.parent_connection()
        b_conn = b.parent_connection()

        # Sever existing connections
        a.remove_connection(a_conn.from_slot)
        b.remove_connection(b_conn.from_slot)

        # Create new connections
        a.set_connection(a_conn.from_slot, b_conn.to_slot, b_conn.node, parent=False)
        b.set_connection(b_conn.from_slot, a_conn.to_slot, a_conn.node, parent=False)

    def add_random_body_part(self, prob, root):
        """
        Generates a new random body part
        :param prob: The calculated probability with which the part should be added
        :param root:
        :return: The added body part
        """
        if not decide(prob):
            return None

        nodes = _node_list(root, root=True)
        inputs, outputs, hidden = root.io_count(nodes)
        usable = self.body_gen.get_allowed_parts(self.body_gen.attach_specs, len(nodes), inputs, outputs)
        if not usable:
            return None

        free = [(node, slot) for node in nodes
                for slot in node.get_free_slots()]

        if not free:
            return None

        # Choose a body part type
        part = BodyPart()
        part.type = self.body_gen.choose_part(usable, root=False)
        type_spec = self.body_gen.spec.get(part.type)
        self.body_gen.initialize_part(type_spec, part, root=False)
        # nw_node = Node(part, , self.body_gen.spec)
        #
        # # Pick a random attachment position
        # node, slot = random.choice(free)
        # """ :type : Node, int """
        # target_slot = self.body_gen.choose_target_slot(type_spec)
        #
        # node.set_connection(slot, target_slot, )

        raise NotImplementedError("TODO Finish implementation")

    def mutate_node_body_parameters(self, node):
        """
        Mutate a single node's body parameters.
        This generates a new random set of parameters for the node, and
        changes the value of each parameter to

        `(1 - e) * old_value + e * new_value`

        Here `e` is the epsilon value defined in the parameter spec, which
        gives an upper bound for the percentual change in parameter value.
        :param node:
        :return:
        """
        spec = self.body_gen.spec.get(node.part.type)
        nw_params = spec.get_epsilon_mutated_parameters(node.part.param, serialize=False)
        spec.set_parameters(node.part.param, nw_params)

    def mutate_node_brain_parameters(self, node):
        """
        :param node:
        :type node: Node
        :return:
        """
        for neuron in node.neurons:
            spec = self.brain_gen.spec.get(neuron.type)
            nw_params = spec.get_epsilon_mutated_parameters(neuron.param, serialize=False)
            spec.set_parameters(neuron.param, nw_params)