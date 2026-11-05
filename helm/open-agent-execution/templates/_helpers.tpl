{{- define "oae.name" -}}
{{- printf "%s-execution" .Release.Name | trunc 45 | trimSuffix "-" -}}
{{- end -}}
{{- define "oae.labels" -}}
app.kubernetes.io/name: open-agent-execution
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service | quote }}
open-agent.io/environment: {{ .Values.environment | quote }}
{{- end -}}
{{- define "oae.validate" -}}
{{- if or (ne .Release.Namespace .Values.namespace) (ne .Values.namespace (printf "open-agent-%s" .Values.environment)) }}{{ fail "Use the environment-specific control namespace" }}{{ end -}}
{{- if ne .Values.executorNamespace (printf "open-agent-executors-%s" .Values.environment) }}{{ fail "Use the dedicated executor namespace" }}{{ end -}}
{{- $prefix := printf "open-agent-%s-" .Values.environment -}}
{{- range $key, $name := dict "supervisor" .Values.supervisor.existingSecret "temporal" .Values.supervisor.temporalSecret "worker" .Values.executor.workerSecret }}
{{- if ne $name (printf "%s%s" $prefix $key) }}{{ fail "Execution credentials must use environment-specific Secret names" }}{{ end -}}
{{- end -}}
{{- range $name := list .Values.supervisor.caConfigMap .Values.executor.caConfigMap }}
{{- if ne $name (printf "%sca" $prefix) }}{{ fail "Use the environment-specific public CA ConfigMap" }}{{ end -}}
{{- end -}}
{{- range $name := concat .Values.imagePullSecrets .Values.executor.imagePullSecrets }}
{{- if not (hasPrefix $prefix $name) }}{{ fail "Registry Secret names must belong to the release environment" }}{{ end -}}
{{- end -}}
{{- range $peer := list .Values.network.control .Values.network.temporal }}
{{- if not $peer.cidr }}
{{- if ne (index $peer.podLabels "open-agent.io/environment") $.Values.environment }}{{ fail "Control and Temporal peers must match the environment label" }}{{ end -}}
{{- end -}}
{{- end -}}
{{- if and .Values.supervisor.disruptionBudget.enabled (ge (int .Values.supervisor.disruptionBudget.minAvailable) (int .Values.supervisor.replicas)) }}{{ fail "Supervisor PDB must permit a disruption" }}{{ end -}}
{{- if and (eq .Values.environment "prod") (or (lt (int .Values.supervisor.replicas) 2) (not .Values.supervisor.disruptionBudget.enabled)) }}{{ fail "Production supervisor requires two replicas and a disruption budget" }}{{ end -}}
{{- end -}}
{{- define "oae.peer" -}}
{{- if .cidr }}
ipBlock: {cidr: {{ .cidr | quote }}}
{{- else }}
namespaceSelector:
  matchLabels: {kubernetes.io/metadata.name: {{ .namespace | quote }}}
podSelector:
  matchLabels: {{- toYaml .podLabels | nindent 4 }}
{{- end -}}
{{- end -}}
