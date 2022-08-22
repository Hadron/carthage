Designing a Good Model
======================

Arguments
*********

Arguments for a model can come from multiple places:

* From the injector hierarchy::

    @inject_autokwargs(verbose_logging=InjectionKey("verbose_logging"))
    class Model(InjectableModel): pass

    class enclave(Enclave):

        verbose_logging = True

        class submodel(Model): pass #gets verbose_logging from its environment

* At instantiation time::

    model = await self.ainjector(Model, verbose_logging='tuesdays_only')

* From within a subclass in a layout::

    class enclave(Enclave):

        class model_instance(Model):

            verbose_logging = 'wednesdays'

In most situations it is strongly desired that all three methods of
argument passing work.

Note there are some interactions between these argument methods.

#.  When arguments are specified in the injector hierarchy, the :class:`Injector` will populate kwargs from the injected dependencies, so when instantiated the model will receive the arguments as kwargs.

#.  In contrast, when arguments are specified within an instance of a model in the modeling language, kwargs are never used.  Instead, properties are set directly on the class that the model inherits from.

#. Arguments specified in a modeling language instance will typically cascade into sub models via the injector hierarchy—the third method of specifying arguments also can become the first::

     class outer_model(Model):

         verbose_logging = 'odd_thursdays' # sets for this instance

         class inner_model(Model):

             # But also sets a value in the injector, so that
             # the inner instance also gets a verbose_logging of 'odd_thursdays'

