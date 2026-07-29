{{- define "agentrail.name" -}}
agentrail
{{- end -}}

{{- define "agentrail.labels" -}}
app.kubernetes.io/name: {{ include "agentrail.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "agentrail.image" -}}
{{ .Values.global.imageRegistry }}/{{ .Values.global.imageNamespace }}/{{ .service }}:{{ .Values.global.imageTag }}
{{- end -}}

{{- define "agentrail.securityContext" -}}
allowPrivilegeEscalation: false
capabilities:
  drop: ["ALL"]
readOnlyRootFilesystem: true
runAsNonRoot: true
runAsUser: 10001
{{- end -}}
