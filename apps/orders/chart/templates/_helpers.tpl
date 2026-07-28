{{- define "orders.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "orders.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/*
Per-component name: orders-gateway, orders-orders, orders-inventory, orders-redis.

Helpers take a dict of (root, component) so four components share one
implementation instead of four near-identical blocks.
*/}}
{{- define "orders.componentName" -}}
{{- printf "%s-%s" (include "orders.fullname" .root) .component | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "orders.selectorLabels" -}}
app.kubernetes.io/name: {{ include "orders.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{- define "orders.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .root.Chart.Name .root.Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "orders.selectorLabels" . }}
app.kubernetes.io/version: {{ .root.Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
app.kubernetes.io/part-of: orders
environment: {{ required "environment is required (set it in envs/<env>.yaml)" .root.Values.environment }}
{{- end }}

{{/*
Environment shared by the two services that own data. Defined once so the pair
cannot drift apart -- if orders and inventory ever pointed at different Redis
instances or databases, events would vanish with no error anywhere.
*/}}
{{- define "orders.backendEnv" -}}
- name: DB_HOST
  value: {{ .Values.database.host | quote }}
- name: DB_PORT
  value: {{ .Values.database.port | quote }}
- name: DB_USER
  value: {{ .Values.database.user | quote }}
- name: DB_NAME
  value: {{ required "database.name is required (set it in envs/<env>.yaml)" .Values.database.name | quote }}
- name: DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.database.passwordSecret.name }}
      key: {{ .Values.database.passwordSecret.key }}
- name: REDIS_HOST
  value: {{ include "orders.componentName" (dict "root" $ "component" "redis") | quote }}
- name: REDIS_PORT
  value: {{ .Values.redis.port | quote }}
{{- end }}
