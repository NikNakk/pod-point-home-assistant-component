"""Tests for Pod Home smart-charging preferences."""

from types import SimpleNamespace

from custom_components.pod_point.select import _tariff_prices


def test_tariff_prices_uses_all_period_rates():
    """Priority choices use the true minimum and maximum tariff rates."""
    coordinator = SimpleNamespace(
        tariffs={
            "PSL-123456": [
                SimpleNamespace(
                    tariff_info=[
                        SimpleNamespace(price=0.30),
                        SimpleNamespace(price=0.07),
                        SimpleNamespace(price=0.18),
                    ]
                )
            ]
        }
    )

    prices = _tariff_prices(coordinator, "PSL-123456")
    assert min(prices) == 0.07
    assert max(prices) == 0.30
