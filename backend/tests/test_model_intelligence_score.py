"""Model intelligence scores are optional presentation metadata, not inference parameters."""

import pytest
from pydantic import ValidationError

from test_model_controls_compat import model_controls_schema


@pytest.mark.parametrize('score', [None, 0, 1, 50, 99])
def test_model_score_round_trips_in_metadata(score):
    form_type = model_controls_schema()['ModelForm']
    form = form_type(id='scored-model', name='Scored model', params={}, meta={'intelligence_score': score})
    restored = form_type.model_validate_json(form.model_dump_json())

    assert restored.meta.intelligence_score == score
    assert 'intelligence_score' not in restored.params.model_dump()


def test_existing_models_have_no_score_and_score_can_be_cleared():
    form_type = model_controls_schema()['ModelForm']
    form = form_type(id='existing-model', name='Existing model', params={}, meta={})
    assert form.meta.intelligence_score is None

    saved = form.model_dump()
    saved['meta']['intelligence_score'] = 99
    scored = form_type.model_validate_json(form_type.model_validate(saved).model_dump_json())
    cleared = scored.model_dump()
    cleared['meta']['intelligence_score'] = None
    assert form_type.model_validate(cleared).meta.intelligence_score is None


@pytest.mark.parametrize('score', [-1, 100, 1.5, 50.0, '50', '', True, False, [], {}])
def test_model_score_rejects_out_of_range_and_non_integer_values(score):
    with pytest.raises(ValidationError) as error:
        model_controls_schema()['ModelForm'](
            id='invalid-score', name='Invalid score', params={}, meta={'intelligence_score': score}
        )
    assert error.value.errors()[0]['loc'] == ('meta', 'intelligence_score')
