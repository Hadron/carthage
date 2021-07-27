Introduction
============

Carthage is an **Infrastructure as Code (IAC)** framework.
Carthage provides models for infrastructure concepts such as :class:`machines <carthage.machine.AbstractMachineModel`, :class:`networks <carthage.Network>`, and domains or groups of machines.  There are concrete implementations of these models including containers and virtual machines.

Carthage allows experts to quickly construct infrastructure from a Carthage layout.  Infrastructure can be real, virtual, or a mixture.  Often the same layout is used to produce both real and virtual infrastructure.  In the core of Carthage, when we have had to choose between power and efficiency for experts or making things easy for beginners, we have chosen to empower experts.  Carthage evolved in part out of frustrations with other IAC frameworks.  On the surface these other frameworks were easy to understand, but they lacked the power to express real world environments.  We found ourselves writing a framework to compile domain-specific models into inputs for these other frameworks.  Rather than combining the complexity of our precompiler with the limitations emposed by other systems, we focus on providing a flexible, powerful framework.


While parts of Carthage are expert tools, Carthage works to keep simple tasks simple.  We strive to make it easy to make simple changes to layouts.  We also strive to allow complexity to be compartmentalized.  It might take a Carthage expert to design a reusable template for describing networking for a complex layout that can be deployed both on virtual hardware and on real switches.  However, anyone who knows Ansible or some other supported devops tool can contribute to a Carthage application.  Adding an Ansible role or playbook to a Carthage machine is easy.

Many IAC systems focus on building containers and micro-services.  By
focusing on these environments, significant simplicity is gained.
Some of the Carthage use cases focus on modeling existing
architectures that are not micro service based.  Many Carthage layouts
do involve at least some portion that is containerized or micro
service based.  However Carthage can also model other architectural approaches.

Carthage permits layouts to be described in a declarative manner when that makes sense.  There are many advantages to declarative descriptions: it is possible to introspect the description, and even to compare the state of real hardware or a cloud environment to the description.
However the real world is rarely that simple.  As an example, a layout may wish to create a machine for each developer in an active directory group.  So as part of building the layout, Carthage needs to query  the directory server.
Such a process cannot be fully declarative.  Things get even more complex when the same layout is responsible for building and maintaining the directory server itself.
Supporting such configurations is a design goal of Carthage.

.. _usecase:testing:

Testing Use Case
********************

One of the motivating applications for Carthage is to provide a realistic test environment for a distributed product that includes hosted and cloud components.
In this mode, all machines are realized in virtual environments (either as containers or VMs).

The goal is to test the product as well as the IAC infrastructure used to install and ship hosted components along with infrastructure to maintain the cloud service.

The testing environment needs to be entirely isolated from the production environment and cloud services.

To accomplish this, a Carthage test layout is constructed.
This layout starts by building initial OS images.
Then it bootstraps some of the components from the IAC layouts used to install real hardware, retargeted at virtual environments.
This is used to set up the cloud services.
IAC code used to maintain the production services is targeted to set up the provisioning and inventory cloud services.
Data is copied in from an export provided by the real cloud service.  The data is massaged to account for a few differences where the test environment does not fully replicate real hardware.  (As an example, connections to the test network are more uniform than connections to networks around the world.)

Then the production IAC code is run in the virtual environment to bring up and provision virtual analogues of real equipment and cloud services.
Tests are run against these systems and the results reported.

Several features emerge from this test case:

* Carthage supports a multi-stage layout.  Until the virtual instance of the provisioning database becomes available, Carthage doesn't even know what virtual systems it will ultimately build.

* Carthage needs to be able to interact with complex networks with potentially overlapping addressing plans.  The test network topology directly mirrors the production topology; many of the key services are at the same address.  Carthage needs to make sure that isolation is maintained.  Carthage needs to function even when the test network is embedded entirely within the production network.  However in limited cases, for example importing the data export, connectivity is required.

* Carthage needs to have facilities to reach into the virtual environment and explore test failures, both for graphical and non-graphical sessions.

.. _usecase:customer_build:

Customer Build Use Case
***********************

  Blah Blah Blah
  
