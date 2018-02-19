from .dependency_injection import inject, Injectable

class ConfigLayout(Injectable):

    image_dir = "/srv/images/test"
    base_container_image = "/usr/share/hadron-installer/hadron-container-image.tar.gz"
    container_prefix = 'carthage-'
    state_dir ="/srv/images/test/state"
    hadron_operations = "/home/hartmans/hadron-operations"
    delete_volumes = False
