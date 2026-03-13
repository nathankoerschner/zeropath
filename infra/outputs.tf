output "backend_url" {
  description = "URL of the API Cloud Run service"
  value       = google_cloud_run_v2_service.backend.uri
}

output "worker_url" {
  description = "URL of the worker Cloud Run service"
  value       = google_cloud_run_v2_service.worker.uri
}

output "frontend_url" {
  description = "URL of the frontend Cloud Run service"
  value       = google_cloud_run_v2_service.frontend.uri
}

output "db_instance_name" {
  description = "Cloud SQL instance connection name"
  value       = google_sql_database_instance.postgres.connection_name
}

output "artifact_registry" {
  description = "Artifact Registry repository path"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}
