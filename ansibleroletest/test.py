import click
import os
import six
import yaml

from .container import ExecuteReturnCodeError
from.utils import pull_image_progress

DEFAULT_CONTAINERS = {
    'centos6': 'centos:6',
    'centos7': 'centos:7',
    'debian-wheezy': 'debian:wheezy',
    'debian-jessie': 'debian:jessie',
    'ubuntu-lts': 'ubuntu:lts',
    'ubuntu15': 'ubuntu:15.04'
}


class Test(object):
    """
    Represents a test object, data should be loaded from tests/<test>.yml
    """

    # internal counter for unnamed tests, just use that counter instead
    _counter = 0

    def __init__(self, framework, test):
        self.framework = framework
        self.docker = self.framework.docker.new()
        self.role_name = self.framework.role_name
        self.test = test
        Test._counter += 1
        self.id = Test._counter

        self.inventory_file = 'inventory_%d' % self.id
        self.playbook_file = 'test_%d.yml' % self.id

    @property
    def inventory(self):
        """
        Returns the inventory content based on the images enabled in the test
        """
        inventory = '[test]\n'
        for name, container in six.iteritems(self.docker.containers):
            inventory += '{0} ansible_ssh_host={1} ansible_ssh_user=ansible ' \
                         'ansible_ssh_pass=ansible\n' \
                .format(name, container.internal_ip)
        return inventory

    @property
    def name(self):
        """
        The test name
        """
        if 'name' in self.test:
            return self.test['name']
        return 'Test #%d' % self.id

    def cleanup(self):
        """
        Destroy all the test containers
        """
        self.framework.print_header('CLEANING TEST CONTAINERS')
        for name, container in six.iteritems(self.docker.containers):
            self.docker.destroy(name)
            click.secho('ok: [%s]' % container.image, fg='green')

    def run(self, extra_vars=None, limit=None, skip_tags=None,
            tags=None, verbosity=None, privileged=False):
        """
        Start the containers and run the test playbook
        :param extra_vars: extra vars to pass to ansible
        :param limit: limit on which targets to run the tests
        :param skip_tags: skip certain tags
        :param tags: run only those tags
        :param verbosity: augment verbosity of ansible
        :param privileged: start containers in privileged mode
        """
        try:
            self.framework.print_header('TEST [%s]' % self.name)
            self.setup(limit, privileged)

            self.framework.print_header('RUNNING TESTS')

            ansible_cmd = [
                'ansible-playbook',
                '-i', os.path.join('/work', self.inventory_file)
            ]

            if extra_vars:
                for extra_var in extra_vars:
                    ansible_cmd += ['--extra-vars', extra_var]

            if limit:
                ansible_cmd += ['--limit', limit]

            if skip_tags:
                ansible_cmd += ['--skip-tags', skip_tags]

            if tags:
                ansible_cmd += ['--tags', tags]

            if verbosity:
                ansible_cmd.append('-%s' % ('v' * verbosity))

            ansible_cmd.append(os.path.join('/work', self.playbook_file))

            self.framework.stream(*ansible_cmd)

            return True
        except ExecuteReturnCodeError as e:
            click.secho(str(e), fg='red')

            return False
        finally:
            self.cleanup()

    def setup(self, limit=None, privileged=False):
        """
        Does the initial container and playbook setup/generation
        :param limit:
        :param privileged:
        """
        self.setup_playbook()
        self.start_containers(limit, privileged)
        self.setup_inventory()

    def setup_playbook(self):
        """
        Extract the playbook from the test file and write it in our
        work directory
        """
        if 'playbook' not in self.test:
            raise NameError('Missing key "playbook" in test file')

        playbook_file = os.path.join(self.framework.work_dir,
                                     self.playbook_file)

        with open(playbook_file, 'w') as fd:
            playbook = yaml.dump(self.test['playbook'])\
                .replace('@ROLE_NAME@', self.role_name)
            fd.write(playbook)

    def setup_inventory(self):
        """
        Generates the inventory based on the created/running containers
        """
        framework_file = os.path.join(self.framework.work_dir,
                                      self.inventory_file)
        with open(framework_file, 'w') as fd:
            fd.write(self.inventory)

    def start_containers(self, limit=None, privileged=False):
        """
        Starts the containers, if not containers are specified in the test
        starts all containers available (centos/debian/ubuntu)
        :param limit: limit which containers to start
        :param privileged: start the containers in privileged mode
        """
        self.framework.print_header('STARTING CONTAINERS')

        # TODO: potentially we'd want to scan the roles' meta file and create
        #       containers based on the advertised supported operating systems,
        #       the issue is that the format is kinda rough, like redhat and
        #       centos are merged under EL, and some distros are not available
        if 'containers' not in self.test:
            self.test['containers'] = DEFAULT_CONTAINERS

        # do not start containers that do not match the given limit
        if limit and limit != 'all':
            unwanted = set(self.test['containers']) - set(limit.split(','))
            for unwanted_container in unwanted:
                del self.test['containers'][unwanted_container]

        for name, image in six.iteritems(self.test['containers']):
            full_image = 'aeriscloud/ansible-%s' % image
            self.docker.create(name, image=full_image).start(
                privileged=privileged,
                progress=pull_image_progress()
            )
            click.secho('ok: [%s]' % full_image, fg='green')
