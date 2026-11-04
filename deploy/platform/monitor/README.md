# Private demonstration monitoring

The existing authenticated workbench is the user interface. Prometheus and
node_exporter have no public listener or ingress route. Host statistics require
the platform administrator role; tenant statistics use `LOG_READ` and a database
snapshot scoped by tenant, environment and explicit time window.

## Install

Run `provision_monitor.py` as the authorized test host administrator after the
control API release containing the `observer` role and migration V1.56 is ready.
Stage the following in the same private directory as the script:

- `monitor_guard.py`, `artifacts.json`, `alerts.yml`, `alerts.test.yml`;
- the two official Linux amd64 archives named in `artifacts.json`.

The installer does not download dependencies. It verifies SHA256 before reading
selected regular archive entries, preserves upstream LICENSE/NOTICE, refuses
foreign directories/services, and tests rules with the pinned official promtool.
Missing or changed archives fail before services are started. Credentials come
from the owned API installation and are neither supplied as CLI arguments nor
printed. A lock serializes monitor installation; updates restart only these
three owned units after configuration checks succeed:

- `open-agent-monitor-guard`: guards TCP 19090 without flushing/reloading the shared firewall;
- `open-agent-node-exporter`: dynamic non-root user, loopback TCP 19100, memory 96 MB, CPU 10%;
- `open-agent-prometheus`: dedicated non-root user, API bridge TCP 19090, memory 384 MB, CPU 30%.

Prometheus retains at most 3 days or 256 MB of blocks (WAL/head and process memory
are additional). Scraping and rule evaluation run every 15 seconds. The host
endpoint accepts connections from the dedicated control API bridge or localhost;
other new connections are dropped. Existing connection replies are unaffected.
The Java API uses `PLATFORM_PROMETHEUS_URL`; no user-supplied URL or PromQL is
forwarded. The node exporter intentionally includes overall host usage, including
other applications, and only the three named platform systemd units.

## Verify and operate

`deploy/platform/control/verify_monitoring.py` checks seven SQL windows through
public HTTPS, four tenant/environment/admin denials, observer authentication,
32 bounded samples, both scrape targets, all seven loaded alerts, a rejected
connection from another owned bridge, and node-exporter stop/restart recovery.
It does not create runs, call models, or stop the execution supervisor.

The rule fixture has 22 assertions: delayed firing and recovery for every rule,
plus an HTTP scrape failure independent of the database collector. Reproduce:

```sh
cd deploy/platform/monitor
/path/to/pinned/promtool check rules alerts.yml
/path/to/pinned/promtool test rules alerts.test.yml
```

Use `systemctl status open-agent-prometheus open-agent-node-exporter` and their
owned journal entries for diagnosis. The private Prometheus `/api/v1/targets`,
`/api/v1/rules` and `/api/v1/alerts` support operator checks. No email/Slack
notification recipient is configured; route these rules through the enterprise
Alertmanager during production integration. Database query failure emits a
fixed warning, sets collection health to zero and exports unavailable values as
NaN. Host data older than 60 seconds or a failed source is withheld from the UI.

This is a single-host demonstration installation. Production must use its own
Prometheus target topology, retention, HA and alert routing. This increment does
not constitute full offline cold-start, production Kubernetes or backup acceptance.

## Official references

- [Prometheus configuration and private header files](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)
- [promtool rule unit tests](https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/)
- [Node exporter guide](https://prometheus.io/docs/guides/node-exporter/)
- [Micrometer official Prometheus registry](https://docs.micrometer.io/micrometer/reference/implementations/prometheus.html)

Context7 was unavailable in the development environment; official documentation,
pinned artifacts and executable tests were used to verify these interfaces.
