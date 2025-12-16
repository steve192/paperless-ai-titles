import typer

from .core.config import get_settings
from .core.logging_config import configure_logging
from .services.jobs import enqueue_document

configure_logging(get_settings())

app = typer.Typer(help="CLI helpers for Paperless AI Titles")


@app.command()
def enqueue(document_id: int, reason: str = typer.Option("cli", help="Reason for enqueue")):
    """Enqueue a document for processing."""
    job, created = enqueue_document(document_id, source="cli", reason=reason)
    status = "created" if created else "existing"
    typer.echo(f"Job {job.id} ({status}) for document {document_id}")


if __name__ == "__main__":
    app()
