#!/usr/bin/env bash
# Deploy the Clinic Broker to Cloud Run. Run from the repo root in Cloud Shell:
#   bash deploy/cloud_run.sh            (uses Fulton)
#   COUNTY_NAME=Cook bash deploy/cloud_run.sh
set -euo pipefail
PROJECT="$(gcloud config get-value project 2>/dev/null)"
REGION="${REGION:-us-central1}"
COUNTY_NAME="${COUNTY_NAME:-Fulton}"
SERVICE="${SERVICE:-clinic-broker}"
echo "Deploying $SERVICE to $PROJECT / $REGION (county: $COUNTY_NAME)"

gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com aiplatform.googleapis.com bigquery.googleapis.com

gcloud run deploy "$SERVICE" \
  --source agent \
  --region "$REGION" \
  --allow-unauthenticated \
  --memory 2Gi --cpu 2 --timeout 600 \
  --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=$PROJECT,GOOGLE_CLOUD_LOCATION=global,MODEL=gemini-3.6-flash,A4I_DATASET=a4i_health,COUNTY_NAME=$COUNTY_NAME"

echo
echo "If the agent can't read BigQuery or call Gemini, grant the runtime service account:"
echo "  SA=\$(gcloud run services describe $SERVICE --region $REGION --format='value(spec.template.spec.serviceAccountName)')"
echo "  for R in roles/bigquery.user roles/bigquery.dataViewer roles/aiplatform.user; do"
echo "    gcloud projects add-iam-policy-binding $PROJECT --member=serviceAccount:\$SA --role=\$R; done"
