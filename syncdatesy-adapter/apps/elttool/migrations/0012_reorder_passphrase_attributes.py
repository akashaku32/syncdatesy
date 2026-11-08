# Generated manually - Reorder passphrase to appear before private_key and public_key

from django.db import migrations


def reorder_passphrase_attributes(apps, schema_editor):
    """
    Reorder passphrase to appear before private_key and public_key
    for both DestinationAttribute and SourceAttribute.
    """
    DestinationAttribute = apps.get_model('elttool', 'DestinationAttribute')
    SourceAttribute = apps.get_model('elttool', 'SourceAttribute')
    
    # ─────────────────────────────────────────────────────────────
    # Reorder DestinationAttribute
    # ─────────────────────────────────────────────────────────────
    print("\n🔄 Reordering DestinationAttribute...")
    
    # Find all attributes that need reordering (for all destinations)
    all_dest_attrs = DestinationAttribute.objects.all().order_by('dst_id', 'dst_attr_id')
    
    # Group by destination
    dest_groups = {}
    for attr in all_dest_attrs:
        if attr.dst_id not in dest_groups:
            dest_groups[attr.dst_id] = []
        dest_groups[attr.dst_id].append(attr)
    
    for dst_id, attrs in dest_groups.items():
        # Find passphrase, private_key, and public_key
        passphrase_attr = None
        private_key_attr = None
        public_key_attr = None
        
        for attr in attrs:
            if attr.attribute_name == 'passphrase':
                passphrase_attr = attr
            elif attr.attribute_name == 'private_key':
                private_key_attr = attr
            elif attr.attribute_name == 'public_key':
                public_key_attr = attr
        
        # Only reorder if all three exist
        if passphrase_attr and private_key_attr and public_key_attr:
            # Get current IDs
            passphrase_id = passphrase_attr.dst_attr_id
            private_key_id = private_key_attr.dst_attr_id
            public_key_id = public_key_attr.dst_attr_id
            
            # Check if passphrase already comes first
            if passphrase_id < private_key_id and passphrase_id < public_key_id:
                print(f"  ✅ Destination {dst_id}: passphrase already in correct position")
                continue
            
            print(f"  🔄 Destination {dst_id}: Reordering passphrase, private_key, public_key")
            print(f"     Current IDs: passphrase={passphrase_id}, private_key={private_key_id}, public_key={public_key_id}")
            
            # Find the minimum ID among the three
            min_id = min(passphrase_id, private_key_id, public_key_id)
            
            # Strategy: Use temporary high IDs to avoid conflicts
            temp_id_base = 999999
            
            # Step 1: Move all three to temporary IDs
            DestinationAttribute.objects.filter(dst_attr_id=passphrase_id).update(dst_attr_id=temp_id_base + 1)
            DestinationAttribute.objects.filter(dst_attr_id=private_key_id).update(dst_attr_id=temp_id_base + 2)
            DestinationAttribute.objects.filter(dst_attr_id=public_key_id).update(dst_attr_id=temp_id_base + 3)
            
            # Step 2: Assign correct IDs in desired order
            DestinationAttribute.objects.filter(dst_attr_id=temp_id_base + 1).update(dst_attr_id=min_id)  # passphrase first
            DestinationAttribute.objects.filter(dst_attr_id=temp_id_base + 2).update(dst_attr_id=min_id + 1)  # private_key second
            DestinationAttribute.objects.filter(dst_attr_id=temp_id_base + 3).update(dst_attr_id=min_id + 2)  # public_key third
            
            print(f"     New IDs: passphrase={min_id}, private_key={min_id + 1}, public_key={min_id + 2}")
    
    # ─────────────────────────────────────────────────────────────
    # Reorder SourceAttribute
    # ─────────────────────────────────────────────────────────────
    print("\n🔄 Reordering SourceAttribute...")
    
    # Find all attributes that need reordering (for all sources)
    all_src_attrs = SourceAttribute.objects.all().order_by('src_id', 'src_attr_id')
    
    # Group by source
    src_groups = {}
    for attr in all_src_attrs:
        if attr.src_id not in src_groups:
            src_groups[attr.src_id] = []
        src_groups[attr.src_id].append(attr)
    
    for src_id, attrs in src_groups.items():
        # Find passphrase, private_key, and public_key
        passphrase_attr = None
        private_key_attr = None
        public_key_attr = None
        
        for attr in attrs:
            if attr.attribute_name == 'passphrase':
                passphrase_attr = attr
            elif attr.attribute_name == 'private_key':
                private_key_attr = attr
            elif attr.attribute_name == 'public_key':
                public_key_attr = attr
        
        # Only reorder if all three exist
        if passphrase_attr and private_key_attr and public_key_attr:
            # Get current IDs
            passphrase_id = passphrase_attr.src_attr_id
            private_key_id = private_key_attr.src_attr_id
            public_key_id = public_key_attr.src_attr_id
            
            # Check if passphrase already comes first
            if passphrase_id < private_key_id and passphrase_id < public_key_id:
                print(f"  ✅ Source {src_id}: passphrase already in correct position")
                continue
            
            print(f"  🔄 Source {src_id}: Reordering passphrase, private_key, public_key")
            print(f"     Current IDs: passphrase={passphrase_id}, private_key={private_key_id}, public_key={public_key_id}")
            
            # Find the minimum ID among the three
            min_id = min(passphrase_id, private_key_id, public_key_id)
            
            # Strategy: Use temporary high IDs to avoid conflicts
            temp_id_base = 999999
            
            # Step 1: Move all three to temporary IDs
            SourceAttribute.objects.filter(src_attr_id=passphrase_id).update(src_attr_id=temp_id_base + 1)
            SourceAttribute.objects.filter(src_attr_id=private_key_id).update(src_attr_id=temp_id_base + 2)
            SourceAttribute.objects.filter(src_attr_id=public_key_id).update(src_attr_id=temp_id_base + 3)
            
            # Step 2: Assign correct IDs in desired order
            SourceAttribute.objects.filter(src_attr_id=temp_id_base + 1).update(src_attr_id=min_id)  # passphrase first
            SourceAttribute.objects.filter(src_attr_id=temp_id_base + 2).update(src_attr_id=min_id + 1)  # private_key second
            SourceAttribute.objects.filter(src_attr_id=temp_id_base + 3).update(src_attr_id=min_id + 2)  # public_key third
            
            print(f"     New IDs: passphrase={min_id}, private_key={min_id + 1}, public_key={min_id + 2}")
    
    print("\n✅ Reordering completed!")


def reverse_reorder_passphrase_attributes(apps, schema_editor):
    """
    Reverse migration - restore original order (optional, for rollback)
    Note: This is a simplified reverse - you may need to adjust based on original IDs
    """
    print("\n⚠️  Reverse migration: Original order cannot be fully restored without original ID values")
    print("   If rollback is needed, restore from database backup")


class Migration(migrations.Migration):

    dependencies = [
        ('elttool', '0011_populate_read_only_values'),
    ]

    operations = [
        migrations.RunPython(reorder_passphrase_attributes, reverse_reorder_passphrase_attributes),
    ]

