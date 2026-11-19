"""Pilot decision policy. Alerts indicate review needs, never a verdict."""
import math



THRESHOLD_KEYS = ('threshold_review', 'threshold_escalate', 'context_min_confidence', 'threshold_fraud')


def validate_policy(policy):
    if policy.get('decision_mode','tiered') not in ['binary','tiered']:
        raise ValueError('decision_mode must be binary or tiered')
    for key in THRESHOLD_KEYS:
        value = policy.get(key, .7 if key == 'context_min_confidence' else .5 if key == 'threshold_fraud' else None)
        if value is None and key not in ['context_min_confidence','threshold_fraud']:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f'{key} must be a finite number between 0 and 1')
    review = policy.get('threshold_review')
    escalate = policy.get('threshold_escalate')
    if escalate is not None and (review is None or escalate < review):
        raise ValueError('threshold_escalate requires threshold_review and must be >= threshold_review')


def resolve_policy(base, overrides):
    """Missing YAML keys inherit the bundle; explicit null disables that threshold."""
    validate_policy(base)
    result = dict(base)
    configured = {key: overrides[key] for key in (*THRESHOLD_KEYS,'decision_mode') if key in overrides}
    result.update(configured)
    validate_policy(result)
    if configured:
        result.update(source='config_override', production_ready=False,
                      reason='Manually configured thresholds for local testing; not validated deployment thresholds.',
                      review_metrics=None, escalate_metrics=None)
        if 'context_min_confidence' in configured:
            result['context_min_confidence_status'] = 'manually_configured'
    return result


def assess(answers, policy):
    score = float(answers['is_fraud']['noul'])
    validate_policy(policy)
    if not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError('Invalid model probability')
    if policy.get('decision_mode','tiered') == 'binary':
        threshold = policy.get('threshold_fraud', .5)
        is_fraud = score >= threshold
        diagnostic = answers.get('context_status') or {}
        if not isinstance(diagnostic,dict):diagnostic = {}
        confidence = diagnostic.get('answer_confidence')
        if not isinstance(confidence,(int,float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            confidence = None
        return {'fraud_score':score,'is_fraud':is_fraud,'decision_mode':'binary','threshold_fraud':threshold,
                'context_status':diagnostic.get('choice'),'context_confidence':confidence,
                'level':'FRAUD' if is_fraud else 'TIDAK_FRAUD',
                'reason':'skor_mencapai_threshold_fraud' if is_fraud else 'skor_di_bawah_threshold_fraud',
                'mode':policy.get('mode','shadow'),'production_ready':False}
    context = answers['context_status']['choice']
    confidence = float(answers['context_status']['answer_confidence'])
    if not math.isfinite(score) or not 0 <= score <= 1 or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError('Invalid model probability')
    if context not in {'risiko_aktif', 'layanan_normal', 'laporan_kejadian', 'konteks_kurang'}:
        raise ValueError('Unknown context')
    validate_policy(policy)
    review = policy.get('threshold_review')
    escalate = policy.get('threshold_escalate')
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
            'level': level, 'reason': reason, 'decision_mode':'tiered', 'mode': policy.get('mode', 'shadow'),
            'production_ready': False}
