# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately, **not** in public issues, pull
requests or discussions:

- through GitHub's [private vulnerability
  reporting](https://github.com/elixir-cloud-aai/foca-plugins/security/advisories/new)
  ("Report a vulnerability" on the repository's Security tab), or
- by email to
  [cloud-service@elixir-europe.org](mailto:cloud-service@elixir-europe.org).

Please include, as far as you can:

- the affected plugin distributions and versions (release or commit);
- the gateway app and FOCA versions, and the relevant plugin configuration
  (without secrets);
- steps to reproduce, ideally a minimal example;
- the impact, i.e., what an attacker can achieve.

## Supported versions

Every plugin is a distribution of its own, with its own versions. Security
fixes are made on `dev` and released in a new version of the latest release
series (the latest minor version) of each affected plugin, and in a new
release of the `foca-plugins` meta distribution that pins it. Older versions
get fixes only for critical issues, at the maintainers' discretion.

## What happens next

The maintainers acknowledge the report, assess it, and, if they confirm it,
develop a fix in private. They publish the fix with a GitHub security advisory
(with a CVE where appropriate), coordinating the timing with the reporter, and
credit reporters who wish to be credited. The project is maintained on a
best-effort basis: there are no guaranteed response or fix times.

## Scope

This policy covers the code of this repository and the distributions
published from it (the plugins and the `foca-plugins` meta distribution).
Vulnerabilities in the plugin engine go to
[FOCA](https://github.com/elixir-cloud-aai/foca/security), those in gateway
apps (e.g., proTES) to their repositories, those in deployments run by third
parties to their operators, and those in dependencies to their projects; tell
us too if they affect plugins of this repository.
