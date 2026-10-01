"""Owner install stages, run in this order.

acquire -> preflight -> prepare -> owner -> activation -> https -> agent;
status observes any stage; remove -> rearm returns to activation.
"""
