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

In most situations, if an argument can be specified in the injector hierarchy, the other two methods of argument passing should also work.
Supporting an argument as either kwargs or specified in a subclass is desired in most cases.  Kwarg (and injector) support may not be needed if subclass support is provided and an argument can be adjusted on an instance after instantiation.

When considering which forms of argument passing are appropriate for a given model, consider two usage scenarios.  A model should be usable in a Python program that instantiates it in a procedural function.  Such usage can easily supply kwargs and can easily set properties on an instance after instantiation.  Models should also be usable in the declarative modeling language.  That usage makes it easy to specify dependencies provided by injectors and to set default values in subclasses.  Specifying kwargs not supplied by dependencies from injectors and adjusting properties after instantiation are more difficult in the declarative language.

Note there are some interactions between these argument methods.

#.  When arguments are specified in the injector hierarchy, the :class:`Injector` will populate kwargs from the injected dependencies, so when instantiated the model will receive the arguments as kwargs.

#.  In contrast, when arguments are specified within an instance of a model in the modeling language, kwargs are never used.  Instead, properties are set directly on the class that the model inherits from.

#. Arguments specified in a modeling language instance will typically cascade into sub models via the injector hierarchy—the third method of specifying arguments also can become the first::

     class outer_model(Model):

         verbose_logging = 'odd_thursdays' # sets for this instance

         class inner_model(Model):

             # But also sets a value in the injector, so that
             # the inner instance also gets a verbose_logging of 'odd_thursdays'


Implementing the Three Argument Strategies
__________________________________________

The following class can take *verbose_logging* either from the injector environment, as a kwarg, or set in subclasses::

    @inject_autokwargs(
    verbose_logging=InjectionKey('verbose_logging', _optional=NotPresent),
    )
    class SomeModel(InjectableModel):

        # do things here
        verbose_logging = False

If the injector environment does not contain *verbose_logging* then no kwarg is specified (because of setting *_optional* to *NotPresent*).  That will permit a subclass to override the default for *verbose_logging* specified in the class.
An explicit kwarg will override the injector environment, as is always the case for :meth:`Injector.__call__`.

Note that because this model is a modeling class, any contained subclass will have *verbose_logging* in its injector environment::

      @inject_autokwargs(
    verbose_logging=InjectionKey('verbose_logging', _optional=NotPresent),
    )
    class SomeModel(InjectableModel):

        # do things here
        verbose_logging = False
        class interior(InjectableModel):
        
            # verbose_logging is set.

