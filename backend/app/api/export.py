"""
Export API endpoints
"""
import io
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.analysis import get_owned_job
from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from mlops.export_formats import ExportManager

logger = logging.getLogger(__name__)

router = APIRouter()

TAXON_COLUMNS = ['Taxon', 'Abundance', 'Confidence', 'Status', 'Rank', 'Lineage']


def taxon_rows(results):
    """Flatten per-taxon results into table rows"""
    return [
        {
            'Taxon': taxon['name'],
            'Abundance': taxon['abundance'],
            'Confidence': f"{taxon['confidence'] * 100:.1f}%",
            'Status': 'Novel' if taxon['isNovel'] else 'Known',
            'Rank': taxon.get('taxonomicRank', 'N/A'),
            'Lineage': ' > '.join(taxon.get('lineage', [])),
        }
        for taxon in results['taxonomicData']
    ]


@router.get("/export/{job_id}")
def export_results(
    job_id: str,
    format: str = 'csv',
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Export analysis results as json, csv, tsv, biom or fasta
    """
    job = get_owned_job(db, job_id, user)
    if job.status != 'completed':
        raise HTTPException(status_code=400, detail="Analysis not completed")

    results = job.results

    if format == 'json':
        content = ExportManager.to_json(results)
        media_type = 'application/json'

    elif format == 'csv':
        content = ExportManager.to_csv(taxon_rows(results), columns=TAXON_COLUMNS)
        media_type = 'text/csv'

    elif format == 'tsv':
        content = ExportManager.to_tsv(taxon_rows(results), columns=TAXON_COLUMNS)
        media_type = 'text/tab-separated-values'

    elif format == 'biom':
        biom = ExportManager.to_biom(
            [
                {'taxon_name': t['name'], 'confidence': t['confidence'], 'rank': t.get('taxonomicRank')}
                for t in results['taxonomicData']
            ],
            metadata={'job_id': job_id, **results.get('metadata', {})}
        )
        # Matrix values are observation counts (abundance), not confidences
        biom['data'] = [[i, 0, float(t['abundance'])] for i, t in enumerate(results['taxonomicData'])]
        content = ExportManager.to_json(biom)
        media_type = 'application/json'

    elif format == 'fasta':
        rows = results.get('predictions', [])
        content = ExportManager.to_fasta([
            {
                'id': row['sequence_id'],
                'sequence': seq,
                'taxon_name': row['predicted_taxon'],
                'confidence': row['confidence'],
            }
            for row, seq in zip(rows, job.sequences or [])
        ]) + "\n"
        media_type = 'text/x-fasta'

    else:
        raise HTTPException(
            status_code=400,
            detail="Unsupported format. Use one of: json, csv, tsv, biom, fasta"
        )

    filename = f'results_{job_id}.{format}'
    return StreamingResponse(
        io.BytesIO(content.encode()),
        media_type=media_type,
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )
