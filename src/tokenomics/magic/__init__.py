"""Magic Formula (Joel Greenblatt) fixed equal-weight strategy profiles.

Unlike the scored profiles (v2/v3/v4), Magic Formula profiles hold a fixed list
of tickers at equal weight. The list comes from a text file and is loaded into
Redis by `magic_job`, after which the standard rebalancer trades it like any
other profile (with the profile's equal-weight rebalancing override).
"""

from tokenomics.magic.holdings import equal_weight_targets, load_holdings

__all__ = ["load_holdings", "equal_weight_targets"]
