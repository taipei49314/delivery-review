"""Trusted producer development implementation, separate from the verifier.

Importing this package never runs a reviewed project. Only the explicit Docker
host executor can launch the isolated, pinned guest.
"""
