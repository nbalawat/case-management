{{- define "caseforge.name" -}}{{ .Release.Name | trunc 40 | trimSuffix "-" }}{{- end -}}
{{- define "caseforge.image" -}}{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}{{- end -}}
{{- define "caseforge.serviceAccount" -}}{{ .Values.serviceAccount.name | default (include "caseforge.name" .) }}{{- end -}}
{{- define "caseforge.labels" -}}
app.kubernetes.io/name: caseforge
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
{{- define "caseforge.selector" -}}
app.kubernetes.io/name: caseforge
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/* What every pod needs to be told, checked once so a bad release fails at install, not at 3am. */}}
{{- define "caseforge.checks" -}}
{{- if not .Values.existingSecret }}{{ fail "existingSecret is required: a Secret holding DATABASE_URL (the chart never writes secrets)" }}{{ end -}}
{{- if not (has .Values.schemaOnMismatch (list "refuse" "rebuild")) }}{{ fail "schemaOnMismatch must be refuse or rebuild" }}{{ end -}}
{{- if and (eq .Values.schemaOnMismatch "rebuild") (not .Values.demoTenants) }}{{ fail "schemaOnMismatch=rebuild drops every table; it is only allowed with demoTenants=true" }}{{ end -}}
{{- if not (has .Values.auth.mode (list "oidc" "demo")) }}{{ fail "auth.mode must be oidc or demo" }}{{ end -}}
{{- if and (eq .Values.auth.mode "demo") .Values.ingress.enabled (not .Values.demoTenants) }}{{ fail "auth.mode=demo lets anyone act as anyone; it cannot be exposed through an ingress unless this is a demo (demoTenants=true)" }}{{ end -}}
{{- if and (not .Values.demoTenants) (not .Values.storage.evidenceUrl) }}{{ fail "storage.evidenceUrl is required: pods keep nothing on local disk" }}{{ end -}}
{{- if not (has .Values.keys.provider (list "transit" "none")) }}{{ fail "keys.provider must be transit or none: pods keep nothing on local disk, so a local keyring would be lost with its data" }}{{ end -}}
{{- if and (eq .Values.keys.provider "transit") (not .Values.keys.transit.addr) (not .Values.demoTenants) }}{{ fail "keys.transit.addr is required with keys.provider=transit (or set keys.provider=none for a demo)" }}{{ end -}}
{{- if and (eq .Values.keys.provider "none") (not .Values.demoTenants) }}{{ fail "keys.provider=none stores customer documents unsealed; it is only allowed with demoTenants=true" }}{{ end -}}
{{- if and .Values.eventBus.url (not (hasPrefix "kafka://" .Values.eventBus.url)) }}{{ fail "eventBus.url must start with kafka://" }}{{ end -}}
{{- if and .Values.temporal.url (not (hasPrefix "temporal://" .Values.temporal.url)) }}{{ fail "temporal.url must start with temporal://" }}{{ end -}}
{{- if not (has .Values.temporal.encryption (list "auto" "required" "off")) }}{{ fail "temporal.encryption must be auto, required or off" }}{{ end -}}
{{- if lt (int .Values.temporal.keyVersion) 1 }}{{ fail "temporal.keyVersion starts at 1" }}{{ end -}}
{{- if and .Values.search.url (not (hasPrefix "opensearch" .Values.search.url)) }}{{ fail "search.url must start with opensearch:// or opensearchs://" }}{{ end -}}
{{- end -}}

{{- define "caseforge.pod" -}}
securityContext: {{- toYaml .Values.podSecurityContext | nindent 2 }}
{{- with .Values.imagePullSecrets }}
imagePullSecrets: {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.nodeSelector }}
nodeSelector: {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.tolerations }}
tolerations: {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.affinity }}
affinity: {{- toYaml . | nindent 2 }}
{{- end }}
volumes:
  - name: tmp
    emptyDir: {}
  - name: data
    emptyDir: {}
{{- with .Values.extraVolumes }}
{{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{- define "caseforge.container" -}}
image: {{ include "caseforge.image" . }}
imagePullPolicy: {{ .Values.image.pullPolicy }}
securityContext: {{- toYaml .Values.securityContext | nindent 2 }}
envFrom:
  - configMapRef: {name: {{ include "caseforge.name" . }}-env}
  - secretRef: {name: {{ .Values.existingSecret }}}
env:
  - {name: HOME, value: /tmp}
  - {name: UV_CACHE_DIR, value: /tmp/uv}
  - {name: UV_NO_SYNC, value: "1"}
volumeMounts:
  - {name: tmp, mountPath: /tmp}
  - {name: data, mountPath: /app/data}  # scratch only: evidence, quarantine and the lake are addressed by URL
{{- with .Values.extraVolumeMounts }}
{{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/* Pod labels: the chart's own plus podLabels, such as azure.workload.identity/use for Azure workload identity. */}}
{{- define "caseforge.podLabels" -}}
{{ include "caseforge.labels" . }}
{{- range $k, $v := .Values.podLabels }}
{{ $k }}: {{ $v | quote }}
{{- end }}
{{- end -}}

{{/* Pod annotations that let a Prometheus set up for discovery find the metrics port. */}}
{{- define "caseforge.scrape" -}}
{{- if .Values.observability.scrapeAnnotations }}
prometheus.io/scrape: "true"
prometheus.io/port: {{ .Values.observability.metricsPort | quote }}
prometheus.io/path: /metrics
{{- end }}
{{- end }}
