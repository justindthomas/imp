#!/usr/bin/env bash

export CMD="vppctl -s /run/vpp/core-cli.sock"

sleep 15

$CMD ip6 nd internal ra-interval 30 15
$CMD ip6 nd internal prefix 2602:f90e:10::/64 infinite
$CMD ip6 nd internal no ra-suppress
