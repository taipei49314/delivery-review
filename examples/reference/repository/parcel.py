"""Small authored subject for delivery-review qualification, Apache-2.0."""


def total_units(quantities):
    """Return the sum of nonnegative integer quantities."""
    if any(type(quantity) is not int or quantity < 0 for quantity in quantities):
        raise ValueError('quantities must be nonnegative integers')
    total = 0
    for quantity in quantities:
        total += quantity
    return total
