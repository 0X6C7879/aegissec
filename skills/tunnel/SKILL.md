---
name: tunnel
description: Use when performing AD pentest tunneling and pivoting, especially with Ligolo-ng, Chisel, frp, proxychains, SSH forwarding, SOCKS relays, reverse tunnels, or when internal reachability is the main blocker.
---

# Tunnel

Use this skill when the next step is blocked by network reachability rather than missing credentials.

## Quick Start

1. Read `references/tunneling.md` for concrete command chains.
2. If tool availability is unknown, read `../shared/references/tool-matrix.md` and run `../shared/scripts/detect_adpwn_toolchain.py`.
3. Build the smallest tunnel that unlocks the target subnet or protocol.
4. Use script helpers in `scripts/` when you need ready-to-paste setup commands:
   - `ligolo_setup.py` - proxy/agent/route/start command planner
   - `chisel_pivot.py` - server/client SOCKS pivot command planner
   - `proxychains_gen.py` - generate proxychains config for SOCKS endpoints

## Selection Rules

- Prefer Ligolo-ng for full-network pivots.
- Prefer Chisel for simple SOCKS or reverse tunnel setups.
- Prefer SSH or socat when living-off-the-land matters more than ergonomics.
- Always document listener, route, and reachable targets.

## Output Discipline

For every tunnel setup, report:

- tunnel type (Ligolo-ng / Chisel / SSH / socat / other)
- listener endpoint and port
- route configuration or SOCKS proxy endpoint
- newly reachable target subnets or hosts
- validation test (ping / port check showing reachability)
- next objective

Keep tunnel configs saved and documented for cleanup.

## When To Switch

Switch away from tunneling when:

- Target subnet is now reachable from attack position
- Credential-based approach becomes viable (you have working creds and network path)
- Need to establish additional tunnels from newly compromised host

Switch to:
- `movement` when valid credentials exist and network path is established
- `adscan` when tunnel enables new recon or enumeration scope

## References

- `references/tunneling.md` - tunnel setup decision trees and commands
- `../shared/references/tool-matrix.md` - fallback tools and substitutions
- `../shared/references/output-conventions.md` - workspace and evidence rules
