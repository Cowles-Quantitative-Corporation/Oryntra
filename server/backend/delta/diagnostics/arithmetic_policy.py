"""D01 diagnostic arithmetic policy v2. Never changes caller Decimal context."""
from decimal import Context, localcontext, ROUND_HALF_EVEN, InvalidOperation, DivisionByZero, Overflow, Underflow
from functools import wraps

SPEC = dict(id='DELTA_DIAGNOSTIC_DECIMAL_V2_P256',precision=256,rounding='ROUND_HALF_EVEN',
            Emin=-9999,Emax=9999,clamp=0,
            traps=['InvalidOperation','DivisionByZero','Overflow','Underflow'],
            inexact='Allowed deterministic HALF_EVEN division for reported ratios; fee quantization explicitly CEILING',
            input_domain='Finite decimal strings <=64 chars, <=28 significant digits, adjusted exponent -18..18, stored exponent -28..18')


def controlled(function):
    @wraps(function)
    def call(*args,**kwargs):
        # Construct a fresh context, including traps/flags. Do not inherit caller
        # precision, rounding, exponent range, flags or trap configuration.
        context=Context(prec=256,rounding=ROUND_HALF_EVEN,Emin=-9999,Emax=9999,
                        capitals=1,clamp=0,traps=[InvalidOperation,DivisionByZero,Overflow,Underflow])
        with localcontext(context):
            return function(*args,**kwargs)
    return call


def validate_external(value):
    from decimal import Decimal
    if type(value) is not str or not 0<len(value)<=64:
        raise ValueError('bounded exact decimal input string')
    number=Decimal(value)
    if not number.is_finite() or len(number.as_tuple().digits)>28 or not -28<=number.as_tuple().exponent<=18 or (number and not -18<=number.adjusted()<=18):
        raise ValueError('unsupported external decimal magnitude/precision')
    return number
