"""Delete-path tests for the Proxmox VE DiffSync models (DB-backed)."""

from django.contrib.contenttypes.models import ContentType
from nautobot.apps.testing import TestCase
from nautobot.dcim.models import Location
from nautobot.extras.models import Tag
from nautobot.virtualization.models import Cluster, VirtualMachine, VMInterface

from .proxmox_fixtures import (
    ProxmoxSyncTestMixin,
    _get_virtual_machine_dict,
    _get_vm_interface_dict,
)


class TestProxmoxDiffSyncModelsDelete(ProxmoxSyncTestMixin, TestCase):
    """Delete-path tests: objects absent from the source are removed from Nautobot."""

    def test_virtual_machine_delete(self):
        """A VM no longer present in the source is deleted; the cluster is preserved."""
        source = self._source()
        self._seed_cluster(source)
        vm = source.virtual_machine(**_get_virtual_machine_dict({"name": "web01"}))
        interface = source.interface(**_get_vm_interface_dict({"name": "net0", "virtual_machine__name": "web01"}))
        source.add(vm)
        source.add(interface)
        vm.add_child(interface)
        source.sync_to(self._nb_adapter())
        self.assertTrue(VirtualMachine.objects.filter(name="web01").exists())

        # Re-sync without the VM.
        empty_source = self._source()
        self._seed_cluster(empty_source)
        empty_source.sync_to(self._nb_adapter())

        self.assertFalse(VirtualMachine.objects.filter(name="web01").exists())
        self.assertFalse(VMInterface.objects.filter(name="net0").exists())
        # Clusters use SKIP_UNMATCHED_DST, so they are preserved.
        self.assertTrue(Cluster.objects.filter(name="TestCluster").exists())

    def test_tag_absent_from_source_is_kept(self):
        """A Tag the source no longer reports is left in Nautobot, not deleted."""
        source = self._source()
        self._seed_cluster(source)
        source.add(source.tag(name="custom-tag", description="temp"))
        source.sync_to(self._nb_adapter())
        self.assertTrue(Tag.objects.filter(name="custom-tag").exists())

        empty_source = self._source()
        self._seed_cluster(empty_source)
        empty_source.sync_to(self._nb_adapter())
        self.assertTrue(Tag.objects.filter(name="custom-tag").exists())

    def test_unrelated_tag_survives_sync(self):
        """A pre-existing Tag the integration never synced keeps existing and stays assigned.

        Covers syncs with Proxmox tag syncing disabled, where the source holds only the SSoT tag.
        """
        unrelated = Tag.objects.create(name="unrelated-tag")
        unrelated.content_types.add(ContentType.objects.get_for_model(Location))
        location = self.config.default_location
        location.tags.add(unrelated)

        source = self._source()
        self._seed_cluster(source)
        source.sync_to(self._nb_adapter())

        self.assertTrue(Tag.objects.filter(name="unrelated-tag").exists())
        self.assertIn(unrelated, Location.objects.get(pk=location.pk).tags.all())
