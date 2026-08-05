{{/*
Application name. Everything is derived from it, so it is required: a chart
that silently defaults its own name produces resources nobody expected.
*/}}
{{- define "app.name" -}}
{{- required "app is required (set it in apps/<app>/values.yaml)" .Values.app | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Per-component name: tasks-backend, orders-gateway, orders-redis.

Every helper below takes a dict of (root, component) rather than the bare
context. That is what lets any number of components share one implementation
instead of duplicating near-identical blocks of labels and selectors.

Usage: {{ include "app.componentName" (dict "root" $ "component" "backend") }}
*/}}
{{- define "app.componentName" -}}
{{- printf "%s-%s" (include "app.name" .root) .component | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Selector labels: the minimal, immutable identity of a component.

Deliberately small. A Deployment's selector is immutable once created, so
anything that changes over time (chart version, app version) must stay out of
here or upgrades start failing.
*/}}
{{- define "app.selectorLabels" -}}
app.kubernetes.io/name: {{ include "app.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Full label set: selector labels plus metadata that may change between releases.
The environment label makes "show me everything in staging" a single selector.
*/}}
{{- define "app.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .root.Chart.Name .root.Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "app.selectorLabels" . }}
app.kubernetes.io/version: {{ .root.Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
app.kubernetes.io/part-of: {{ include "app.name" .root }}
environment: {{ required "environment is required (set it in envs/<env>.yaml)" .root.Values.environment }}
{{- end }}

{{/*
Environment variables for one component, in a fixed order: database block,
Redis block, then whatever the component declares itself.

Defined once so that components which share a dependency cannot drift apart --
if two services ever pointed at different Redis instances or databases, events
would vanish with no error anywhere.

The declared `env` list is rendered through `tpl`, so a value may reference
another component by name (an upstream URL, for instance) without the chart
knowing anything about that application's topology.
*/}}
{{- define "app.componentEnv" -}}
{{- $root := .root -}}
{{- $c := .component -}}
{{- if $c.database }}
- name: DB_HOST
  value: {{ $root.Values.database.host | quote }}
- name: DB_PORT
  value: {{ $root.Values.database.port | quote }}
- name: DB_USER
  value: {{ $root.Values.database.user | quote }}
- name: DB_NAME
  value: {{ required "database.name is required (set it in envs/<env>.yaml)" $root.Values.database.name | quote }}
# Read from a Secret that exists only in the cluster, never in Git.
- name: DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $root.Values.database.passwordSecret.name }}
      key: {{ $root.Values.database.passwordSecret.key }}
{{- end }}
{{- if $c.redis }}
- name: REDIS_HOST
  value: {{ include "app.componentName" (dict "root" $root "component" "redis") | quote }}
- name: REDIS_PORT
  value: {{ $root.Values.redis.port | quote }}
{{- end }}
{{- with $c.env }}
{{- tpl (toYaml .) $root | nindent 0 }}
{{- end }}
{{- end }}

{{/*
Probe block for one component. Defaults are the convention this platform
expects an HTTP service to follow: /health says the process is alive, /ready
says it can serve. A component that differs overrides only what differs.

Liveness never checks a dependency: losing Postgres must remove the pod from
its Service, not restart it in a loop.
*/}}
{{- define "app.probes" -}}
{{- $p := .probes | default dict -}}
livenessProbe:
  httpGet:
    path: {{ dig "liveness" "path" "/health" $p }}
    port: http
  initialDelaySeconds: {{ dig "liveness" "initialDelaySeconds" 10 $p }}
  periodSeconds: {{ dig "liveness" "periodSeconds" 15 $p }}
readinessProbe:
  httpGet:
    path: {{ dig "readiness" "path" "/ready" $p }}
    port: http
  initialDelaySeconds: {{ dig "readiness" "initialDelaySeconds" 5 $p }}
  periodSeconds: {{ dig "readiness" "periodSeconds" 10 $p }}
{{- end }}
