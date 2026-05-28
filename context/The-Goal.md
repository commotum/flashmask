THE GOAL:  

* **Enable state-autoregressive next-state prediction:** predict state (t+1) from a timestep-causal context over states (\le t), rather than enforcing causality purely through flat token order.
* **Use kernel-native sparse attention:** encode the spatiotemporal visibility pattern directly in the attention kernel so disallowed future-state interactions are skipped during score computation, not computed densely and then masked out.