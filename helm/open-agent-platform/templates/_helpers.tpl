{{- define "oa.name" -}}
{{- printf "%s-platform" .Release.Name | trunc 48 | trimSuffix "-" -}}
{{- end -}}
{{- define "oa.labels" -}}
app.kubernetes.io/name: open-agent-platform
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service | quote }}
open-agent.io/environment: {{ .Values.environment | quote }}
{{- end -}}
{{- define "oa.validate" -}}
{{- if ne .Release.Namespace .Values.namespace }}{{ fail "Release namespace must equal the reviewed environment namespace" }}{{ end -}}
{{- if ne .Values.namespace (printf "open-agent-%s" .Values.environment) }}{{ fail "Dedicated open-agent-test/open-agent-prod namespaces are required" }}{{ end -}}
{{- if ne .Values.existingSecret (printf "open-agent-%s-control" .Values.environment) }}{{ fail "Use the environment-specific existing Secret" }}{{ end -}}
{{- if ne .Values.identity.issuer (printf "https://%s/identity" .Values.publicHost) }}{{ fail "OIDC issuer must equal https://publicHost/identity" }}{{ end -}}
{{- if hasPrefix "https://" .Values.identity.upstream }}{{- if not .Values.trustBundle.configMapName }}{{ fail "HTTPS identity upstream requires the reviewed CA bundle" }}{{ end }}{{ end -}}
{{- if .Values.executionAdmission }}{{ fail "Cluster execution adapter is not delivered; admission must remain disabled" }}{{ end -}}
{{- if .Values.apiTls.enabled }}
{{- if ne .Values.apiTls.existingSecret (printf "open-agent-%s-api-tls" .Values.environment) }}{{ fail "API TLS requires the environment-specific existing Secret" }}{{ end -}}
{{- if not .Values.trustBundle.configMapName }}{{ fail "API TLS requires the reviewed CA bundle" }}{{ end -}}
{{- end -}}
{{- range $required := list "api/database" "api/storage" "api/identity" "web/identity" }}
{{- $found := false }}
{{- range $.Values.network.dependencies }}{{ if eq (printf "%s/%s" .component .purpose) $required }}{{ $found = true }}{{ end }}{{ end -}}
{{- if not $found }}{{ fail (printf "Missing explicit dependency network rule: %s" $required) }}{{ end -}}
{{- end -}}
{{- range .Values.network.dependencies }}
{{- if and (not .cidr) (has .purpose (list "database" "identity" "storage")) }}
{{- if ne (index .podLabels "open-agent.io/environment") $.Values.environment }}{{ fail "Stateful dependency pod peers must match the release environment label" }}{{ end -}}
{{- end -}}
{{- end -}}
{{- range .Values.network.apiClients }}
{{- if ne (index .podLabels "open-agent.io/environment") $.Values.environment }}{{ fail "Trusted API clients must match the release environment label" }}{{ end -}}
{{- end -}}
{{- range $component := list "api" "web" }}
{{- $settings := index $.Values $component }}
{{- if and $settings.disruptionBudget.enabled (ge (int $settings.disruptionBudget.minAvailable) (int $settings.replicas)) }}{{ fail "PDB minAvailable must be below the configured replica count" }}{{ end -}}
{{- end -}}
{{- if .Values.api.autoscaling.enabled }}
{{- if lt (int .Values.api.autoscaling.maxReplicas) (int .Values.api.autoscaling.minReplicas) }}{{ fail "HPA maxReplicas must be >= minReplicas" }}{{ end -}}
{{- if and .Values.api.disruptionBudget.enabled (ge (int .Values.api.disruptionBudget.minAvailable) (int .Values.api.autoscaling.minReplicas)) }}{{ fail "PDB must allow disruption at HPA minimum replicas" }}{{ end -}}
{{- end -}}
{{- if eq .Values.environment "prod" }}
{{- if or (lt (int .Values.api.replicas) 2) (lt (int .Values.web.replicas) 2) }}{{ fail "Production control/web require at least two replicas" }}{{ end -}}
{{- if or (not .Values.api.disruptionBudget.enabled) (not .Values.web.disruptionBudget.enabled) }}{{ fail "Production control/web require disruption budgets" }}{{ end -}}
{{- end -}}
{{- end -}}
{{- define "oa.peer" -}}
{{- if .cidr }}
ipBlock:
  cidr: {{ .cidr | quote }}
{{- else }}
namespaceSelector:
  matchLabels:
    kubernetes.io/metadata.name: {{ .namespace | quote }}
podSelector:
  matchLabels:
    {{- toYaml .podLabels | nindent 4 }}
{{- end }}
{{- end -}}
