# WES plugins

Plugins for gateways of the [GA4GH Workflow Execution Service (WES)
API](https://github.com/ga4gh/workflow-execution-service-schemas), such as
proWES (`apis = frozenset({"wes"})`): they read or change WES runs, which
FOCA's plugin API gives them in the same shape in every gateway
(`foca.plugins.wes`, on FOCA's `dev` branch until its next release).

Subgroups, with their plugins as `<subgroup>/<name>/`:

- `routing`: choose and order the backends of runs
  - [`workflow-type`](routing/workflow-type/): the backends whose service
    info lists the run's workflow type and engine, with their versions
- `workflow`: fetch workflows from elsewhere and attach them to runs
  - [`trs`](workflow/trs/): from a GA4GH TRS (`trs://` URIs)
  - [`git`](workflow/git/): from public Git repositories of GitHub,
    GitLab and Bitbucket (URLs of files at a ref)

See [`plugins/README.md`](../README.md) for the groups and subgroups.
