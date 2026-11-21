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
{{- if and .Values.eventBus.url (not (hasPrefix "kafka://" .Values.eventBus.url)) }}{{ fail "eventBus.url must start with kafka://" }}{{ end -}}
{{- if and .Values.temporal.url (not (hasPrefix "temporal://" .Values.temporal.url)) }}{{ fail "temporal.url must start with temporal://" }}{{ end -}}
{{- if and .Values.search.url (not (hasPrefix "opensearch" .Values.search.url)) }}{{ fail "search.url must start with opensearch:// or opensearchs://" }}{{ end -}}
{{- end -}}

{{- define "caseforge.pod" -}}
serviceAccountName: {{ include "caseforge.serviceAccount" . }}
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
{{- end -}}
