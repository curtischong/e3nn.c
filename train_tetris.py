# Adapted from https://github.com/e3nn/e3nn-jax/blob/main/examples/tetris_point.py
#  * removed scalar non-linearity for now
#  * added exports to .bin and .mp files for weights
import struct
import time

import numpy as np
import flax
import flax.serialization
import jax
import jax.numpy as jnp
import jraph
import optax

import e3nn_jax as e3nn


def tetris() -> jraph.GraphsTuple:
    pos = [
        [[0, 0, 0], [0, 0, 1], [1, 0, 0], [1, 1, 0]],  # chiral_shape_1
        [[1, 1, 1], [1, 1, 2], [2, 1, 1], [2, 0, 1]],  # chiral_shape_2
        [[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]],  # square
        [[0, 0, 0], [0, 0, 1], [0, 0, 2], [0, 0, 3]],  # line
        [[0, 0, 0], [0, 0, 1], [0, 1, 0], [1, 0, 0]],  # corner
        [[0, 0, 0], [0, 0, 1], [0, 0, 2], [0, 1, 0]],  # L
        [[0, 0, 0], [0, 0, 1], [0, 0, 2], [0, 1, 1]],  # T
        [[0, 0, 0], [1, 0, 0], [1, 1, 0], [2, 1, 0]],  # zigzag
    ]
    pos = jnp.array(pos, dtype=jnp.float32)
    labels = jnp.arange(8)

    graphs = []

    for p, l in zip(pos, labels):
        senders, receivers = e3nn.radius_graph(p, 1.1)

        graphs += [
            jraph.GraphsTuple(
                nodes=p.reshape((4, 3)),  # [num_nodes, 3]
                edges=None,
                globals=l[None],  # [num_graphs]
                senders=senders,  # [num_edges]
                receivers=receivers,  # [num_edges]
                n_node=jnp.array([len(p)]),  # [num_graphs]
                n_edge=jnp.array([len(senders)]),  # [num_graphs]
            )
        ]

    return jraph.batch(graphs)


class Layer(flax.linen.Module):
    target_irreps: e3nn.Irreps
    denominator: float
    sh_lmax: int = 3

    @flax.linen.compact
    def __call__(self, graphs, positions):
        target_irreps = e3nn.Irreps(self.target_irreps)

        def update_edge_fn(edge_features, sender_features, receiver_features, globals):
            sh = e3nn.spherical_harmonics(
                list(range(1, self.sh_lmax + 1)),
                positions[graphs.receivers] - positions[graphs.senders],
                True,
            )
            print("sender_features.shape")
            print(sender_features.shape)
            tp = e3nn.tensor_product(sender_features, sh).regroup()
            print("tp.shape") # (50, 1440)
            print(tp.shape)
            messages = e3nn.concatenate([sender_features, tp]).regroup() 
            print("messages.shape") # (50, 1536) - they make no attempt to limit the number of channels of the tensor product
            print(messages.shape)
            return messages 

        def update_node_fn(node_features, _sender_features, receiver_features, _globals):
            node_feats = receiver_features / self.denominator
            print("node_feats.shape") # (32, 1536)
            print(node_feats.shape)
            node_feats = e3nn.flax.Linear(target_irreps, name="linear")(node_feats)
            # NOTE: removed scalar activation and extra linear layer for now
            shortcut = e3nn.flax.Linear(
                node_feats.irreps, name="shortcut", force_irreps_out=True
            )(node_features)
            return shortcut + node_feats
        return jraph.GraphNetwork(update_edge_fn, update_node_fn)(graphs)


class Model(flax.linen.Module):
    @flax.linen.compact
    def __call__(self, graphs):
        positions = e3nn.IrrepsArray("1o", graphs.nodes)
        graphs = graphs._replace(nodes=jnp.ones((len(positions), 1)))

        layers = 2 * ["32x0e + 32x0o + 8x1o + 8x1e + 8x2e + 8x2o"] + ["0o + 7x0e"]

        for irreps in layers:
            graphs = Layer(irreps, 1.5)(graphs, positions)

        # Readout logits
        pred = e3nn.scatter_sum(
            graphs.nodes.array, nel=graphs.n_node
        )  # [num_graphs, 1 + 7]
        odd, even1, even2 = pred[:, :1], pred[:, 1:2], pred[:, 2:]
        logits = jnp.concatenate([odd * even1, -odd * even1, even2], axis=1)
        assert logits.shape == (len(graphs.n_node), 8)  # [num_graphs, num_classes]

        return logits


def train(steps=200):
    model = Model()

    # Optimizer
    opt = optax.adam(learning_rate=0.01)

    def loss_fn(params, graphs):
        logits = model.apply(params, graphs)
        labels = graphs.globals  # [num_graphs]

        loss = optax.softmax_cross_entropy_with_integer_labels(logits, labels)
        loss = jnp.mean(loss)
        return loss, logits

    @jax.jit
    def update_fn(params, opt_state, graphs):
        grad_fn = jax.grad(loss_fn, has_aux=True)
        grads, logits = grad_fn(params, graphs)
        labels = graphs.globals
        accuracy = jnp.mean(jnp.argmax(logits, axis=1) == labels)

        updates, opt_state = opt.update(grads, opt_state)
        params = optax.apply_updates(params, updates)
        return params, opt_state, accuracy

    # dataset
    graphs = tetris()

    # initialize
    init = jax.jit(model.init)
    params = init(jax.random.PRNGKey(0), graphs)
    opt_state = opt.init(params)

    # compile jit
    wall = time.perf_counter()
    print("compiling...", flush=True)
    _, _, accuracy = update_fn(params, opt_state, graphs)
    print(f"initial accuracy = {100 * accuracy:.0f}%", flush=True)
    print(f"compilation took {time.perf_counter() - wall:.1f}s")

    # train
    wall = time.perf_counter()
    print("training...", flush=True)
    for _ in range(steps):
        params, opt_state, accuracy = update_fn(params, opt_state, graphs)

        if accuracy == 1.0:
           break

    print(f"final accuracy = {100 * accuracy:.0f}%")

    # serialize for run_tetris.py
    with open("tetris.mp", "wb") as f:
        f.write(flax.serialization.to_bytes(params))
    
    # serialize for tetris.c
    with open("tetris.bin", "wb") as f:
        for layer in range(3):
            for weights in ["linear", "shortcut"]:
                weight = params["params"][f"Layer_{layer}"][weights]
                weight = np.concatenate([w.ravel() for w in weight.values()])
                f.write(struct.pack(f"{len(weight)}f", *weight))





if __name__ == "__main__":
    with jax.disable_jit():
        train()