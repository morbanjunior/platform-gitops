{{/*
Base name of the chart.
*/}}
{{- define "tasks.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Release-qualified base name shared by every component.
*/}}
{{- define "tasks.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else if contains .Chart.Name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/*
Per-component name: tasks-backend, tasks-frontend.

Every helper below takes a dict of (root, component) rather than the bare
context. That is what lets backend and frontend share one implementation
instead of duplicating near-identical blocks of labels and selectors.

Usage: {{ include "tasks.componentName" (dict "root" $ "component" "backend") }}
*/}}
{{- define "tasks.componentName" -}}
{{- printf "%s-%s" (include "tasks.fullname" .root) .component | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Selector labels: the minimal, immutable identity of a component.

Deliberately small. A Deployment's selector is immutable once created, so
anything that changes over time (chart version, app version) must stay out of
here or upgrades start failing.
*/}}
{{- define "tasks.selectorLabels" -}}
app.kubernetes.io/name: {{ include "tasks.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Full label set: selector labels plus metadata that may change between releases.
The environment label makes "show me everything in staging" a single selector.
*/}}
{{- define "tasks.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .root.Chart.Name .root.Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "tasks.selectorLabels" . }}
app.kubernetes.io/version: {{ .root.Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
app.kubernetes.io/part-of: tasks
environment: {{ required "environment is required (set it in envs/<env>.yaml)" .root.Values.environment }}
{{- end }}
