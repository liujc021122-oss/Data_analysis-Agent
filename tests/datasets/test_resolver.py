from uuid import uuid4

import pytest

from data_analysis_agent.datasets.errors import DatasetAccessDeniedError


def test_resolver_rejects_a_dataset_owned_by_another_user(uploaded_dataset):
    resolver, dataset_id, owner_id = uploaded_dataset

    with pytest.raises(DatasetAccessDeniedError):
        resolver.open_for_user(dataset_id, owner_id=uuid4())

    assert resolver.profile_for_user(dataset_id, owner_id=owner_id).row_count == 1
