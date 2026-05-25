from types import SimpleNamespace

from staging.serializers import DedupResultSerializer


class TestDedupResultSerializer:
    def test_get_loser_nome(self):
        serializer = DedupResultSerializer()

        loser = SimpleNamespace(
            nome="LOSER",
            source="coresso",
        )

        obj = SimpleNamespace(loser=loser)

        assert serializer.get_loser_nome(obj) == "LOSER"

    def test_get_loser_nome_without_loser(self):
        serializer = DedupResultSerializer()

        obj = SimpleNamespace(loser=None)

        assert serializer.get_loser_nome(obj) is None

    def test_get_loser_source(self):
        serializer = DedupResultSerializer()

        loser = SimpleNamespace(
            nome="LOSER",
            source="coresso",
        )

        obj = SimpleNamespace(loser=loser)

        assert serializer.get_loser_source(obj) == "coresso"

    def test_get_loser_source_without_loser(self):
        serializer = DedupResultSerializer()

        obj = SimpleNamespace(loser=None)

        assert serializer.get_loser_source(obj) is None