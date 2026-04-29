# Terraform state lives in GCS so multi-developer / CI workflows don't fight.
# The bucket is bootstrapped manually (one-shot) before the first `terraform init`:
#
#   gcloud storage buckets create gs://ps2o-surplusas-tf-state \
#     --project=ps2o-surplusas-api \
#     --location=us-central1 \
#     --uniform-bucket-level-access
#   gcloud storage buckets update gs://ps2o-surplusas-tf-state --versioning
#
# Then init with the bucket pinned:
#
#   terraform init \
#     -backend-config="bucket=ps2o-surplusas-tf-state" \
#     -backend-config="prefix=agents/"

terraform {
  backend "gcs" {
    # bucket / prefix supplied via -backend-config; do not hardcode here.
  }
}
