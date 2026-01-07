"""
Validation functions for IMP configuration.

IP address and CIDR validation utilities.
"""

import ipaddress


def validate_ipv4(ip: str) -> bool:
    """Validate an IPv4 address."""
    try:
        ipaddress.IPv4Address(ip)
        return True
    except ipaddress.AddressValueError:
        return False


def validate_ipv4_cidr(cidr: str) -> bool:
    """Validate an IPv4 CIDR notation."""
    try:
        ipaddress.IPv4Network(cidr, strict=False)
        return True
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError):
        return False


def validate_ipv6(ip: str) -> bool:
    """Validate an IPv6 address."""
    try:
        ipaddress.IPv6Address(ip)
        return True
    except ipaddress.AddressValueError:
        return False


def validate_ipv6_cidr(cidr: str) -> bool:
    """Validate an IPv6 CIDR notation."""
    try:
        ipaddress.IPv6Network(cidr, strict=False)
        return True
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError):
        return False


def parse_cidr(cidr: str) -> tuple[str, int]:
    """Parse CIDR notation into address and prefix."""
    addr, prefix = cidr.rsplit("/", 1)
    return addr, int(prefix)
