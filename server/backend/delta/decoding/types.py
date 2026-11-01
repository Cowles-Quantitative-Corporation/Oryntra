"""Accepted compact-book and reported-T shapes, without forecast producer."""
from dataclasses import dataclass
NANO=10**9
@dataclass(frozen=True, slots=True)
class CompactBook:
    """Caller-attested summary of one actual validated completed depth book.

    depth5 sums must be sums of the first min(5, observed levels) actual sizes.
    This summary cannot prove the ordering/validity of discarded deeper prices.
    source_records are the actual current event's lineage, never reconstructed.
    """
    identity: str
    symbol: str
    source: str
    session: str
    available_ns: int
    event_ns: int
    source_index: int
    feed_instrument_id: int | None
    valid: bool
    reset: bool
    bid_price: int = 0
    ask_price: int = 0
    bid_size: int = 0
    ask_size: int = 0
    bid_depth5: int = 0
    ask_depth5: int = 0
    bid_levels: int = 0
    ask_levels: int = 0
    source_records: tuple = ()

    def __post_init__(self):
        if any(type(v) is not str or not v for v in (self.identity,self.symbol,self.source,self.session)):
            raise ValueError('source identity missing')
        if any(type(v) is not int or v<0 for v in (self.available_ns,self.event_ns,self.source_index)) or self.event_ns>self.available_ns:
            raise ValueError('invalid source clocks/order')
        if self.feed_instrument_id is not None and (type(self.feed_instrument_id) is not int or self.feed_instrument_id<0):
            raise ValueError('invalid feed id')
        if type(self.valid) is not bool or type(self.reset) is not bool:
            raise ValueError('boolean flags required')
        if type(self.source_records) is not tuple or any(type(r) is not tuple or len(r)!=7 or any(type(v) is not int or v<0 for v in r) for r in self.source_records):
            raise ValueError('invalid current source lineage')
        if self.valid:
            if any(type(v) is not int or v<=0 for v in (self.bid_price,self.ask_price,self.bid_size,self.ask_size,self.bid_depth5,self.ask_depth5,self.bid_levels,self.ask_levels)):
                raise ValueError('positive integer compact geometry required')
            if self.bid_price>=self.ask_price or max(self.bid_levels,self.ask_levels)>10 or self.bid_depth5<self.bid_size or self.ask_depth5<self.ask_size:
                raise ValueError('invalid compact geometry')

    @classmethod
    def from_validated_depth_event(cls, event):
        """Project a caller-validated native DepthEvent, without retaining it."""
        if not event.valid:
            return cls(event.identity,event.symbol,event.source,event.session,event.available_ns,event.event_ns,event.source_index,event.feed_instrument_id,False,event.reset,source_records=event.source_records)
        b,a=event.bids[0],event.asks[0]
        return cls(event.identity,event.symbol,event.source,event.session,event.available_ns,event.event_ns,event.source_index,event.feed_instrument_id,True,event.reset,
                   b.price,a.price,b.size,a.size,sum(x.size for x in event.bids[:5]),sum(x.size for x in event.asks[:5]),len(event.bids),len(event.asks),event.source_records)

    @property
    def mid2(self):
        if not self.valid:raise ValueError('unavailable midpoint')
        return self.bid_price+self.ask_price

@dataclass(frozen=True)
class Trade:
 index:int
 recv:int
 price:int
 shares:int
 side:str

@dataclass(frozen=True)
class Group:
 book:CompactBook
 first_index:int
 first_recv:int
 flags:int
 count:int
 trades:tuple
