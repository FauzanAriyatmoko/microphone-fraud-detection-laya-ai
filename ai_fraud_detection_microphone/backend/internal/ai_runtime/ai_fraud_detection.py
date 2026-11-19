"""Pilot decision policy. Alerts indicate review needs, never a verdict."""
import math


def assess(answers, policy):
    score = float(answers['is_fraud']['noul'])
    context = answers['context_status']['choice']
    confidence = float(answers['context_status']['answer_confidence'])
    if not math.isfinite(score) or not 0 <= score <= 1 or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError('Invalid model probability')
    if context not in {'risiko_aktif', 'layanan_normal', 'laporan_kejadian', 'konteks_kurang'}:
        raise ValueError('Unknown context')
    review = policy.get('threshold_review')
    escalate = policy.get('threshold_escalate')
    if review is not None and not 0 <= review <= 1:
        raise ValueError('Invalid review threshold')
    if escalate is not None and (review is None or not review <= escalate <= 1):
        raise ValueError('Invalid escalation threshold')
    if confidence < policy.get('context_min_confidence', .7):
        level, reason = 'PERLU_DICEK', 'confidence_konteks_rendah'
    elif context == 'laporan_kejadian':
        level, reason = 'PERLU_DICEK', 'tindak_lanjut_pengaduan'
    elif context == 'konteks_kurang':
        level, reason = 'PERLU_DICEK', 'minta_klarifikasi'
    elif review is None:
        level, reason = 'PERLU_DICEK', 'ambang_belum_memenuhi_target'
    elif context == 'layanan_normal' and score >= review:
        level, reason = 'PERLU_DICEK', 'keluaran_model_bertentangan'
    elif context == 'risiko_aktif':
        if escalate is not None and score >= escalate:
            level, reason = 'ESKALASI', 'indikasi_aktif_untuk_supervisor'
        else:
            level, reason = 'PERLU_DICEK', 'indikasi_aktif_perlu_verifikasi'
    else:
        level, reason = 'TIDAK_ADA_INDIKASI', 'layanan_normal'
    return {'fraud_score': score, 'context_status': context, 'context_confidence': confidence,
            'level': level, 'reason': reason, 'mode': policy.get('mode', 'shadow'),
            'production_ready': False}
