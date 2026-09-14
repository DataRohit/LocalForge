"""Unit tests for the confirming channel layer.

Covers the transition that makes a join a delivery barrier, the withdrawal of a subscription the
instance never confirmed, the rejoin that must survive a concurrent departure, and the layer
substitution that installs all of it.
"""

import asyncio

import pytest

from config import channels

HOST = {"host": "valkey-channels-vh8dm", "port": 6379}
PREFIX = "localforge"


class FakeLayer:
    """A layer exposing only what the shard connection reads from it.

    Stands in for the per-event-loop layer so a shard can be exercised without building the whole
    channel layer, which would need a socket the unit layer forbids.

    Inherits from `object`.

    Attributes:
        prefix: Project prefix the probe names are built from.
        groups: Members of each group channel, as the real layer tracks them.

    Members:
        None.
    """

    def __init__(self) -> None:
        """Start with the project prefix and no group members.

        Gives the shard the two pieces of layer state it reads, so a test can state group
        membership directly rather than driving it through the layer's API.

        Arguments:
            None.

        Returns:
            None.
        """
        self.prefix = PREFIX
        self.groups: dict[str, set[str]] = {}


class FakePubSub:
    """A publish and subscribe handle that records what it was asked to subscribe to.

    Stands in for the client's handle so a test can observe the commands the layer writes without
    a socket, which the unit layer forbids.

    Inherits from `object`.

    Attributes:
        subscribed: Channel names passed to subscribe, in order.
        unsubscribed: Channel names passed to unsubscribe, in order.
        fail_unsubscribe: Whether unsubscribe should raise instead of recording.

    Members:
        subscribe: Record a subscription request.
        unsubscribe: Record an unsubscription request.
    """

    def __init__(self) -> None:
        """Start with nothing recorded.

        Gives each test a handle whose history belongs to that test alone, so assertions about
        ordering cannot be satisfied by an earlier one.

        Arguments:
            None.

        Returns:
            None.
        """
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.fail_unsubscribe = False

    async def subscribe(self, channel: str) -> None:
        """Record a subscription request.

        Mirrors the client, which writes the command and returns without reading the reply, which
        is the behaviour the confirmation exists to compensate for.

        Arguments:
            channel: Name the layer asked to subscribe to.

        Returns:
            None.
        """
        self.subscribed.append(channel)

    async def unsubscribe(self, channel: str) -> None:
        """Record an unsubscription request.

        Lets a test prove a probe or an unconfirmed subscription is taken back rather than left
        behind, or model a connection that has already gone away.

        Arguments:
            channel: Name the layer asked to unsubscribe from.

        Returns:
            None.

        Raises:
            ConnectionError: If this handle is set to fail its unsubscriptions.
        """
        if self.fail_unsubscribe:
            message = "the connection is gone"
            raise ConnectionError(message)

        self.unsubscribed.append(channel)


class FakeInstance:
    """An instance that answers subscriber-count queries from a script.

    Returns a prepared answer per call so a test can model the instance registering a subscription
    late, immediately, or never.

    Inherits from `object`.

    Attributes:
        answers: Replies to return, one per call, the last repeating once exhausted.
        queried: Channel names the layer asked about, in order.
        hold: Event a reply waits on, used to keep a confirmation in flight.

    Members:
        pubsub_numsub: Return the next scripted subscriber count.
    """

    def __init__(self, answers: list[list[tuple[bytes, int]]]) -> None:
        """Load the scripted answers.

        Takes the replies in the order they should be returned, so a test states the instance's
        behaviour as data rather than as control flow.

        Arguments:
            answers: Replies to return, one per call.

        Returns:
            None.
        """
        self.answers = answers
        self.queried: list[str] = []
        self.hold: asyncio.Event | None = None

    async def pubsub_numsub(self, channel: str) -> list[tuple[bytes, int]]:
        """Return the next scripted subscriber count.

        Waits on the hold when one is set, so a test can keep one confirmation in flight while
        another caller acts on the same channel, and repeats the final answer once the script is
        exhausted.

        Arguments:
            channel: Name the layer asked about.

        Returns:
            The next scripted reply.
        """
        self.queried.append(channel)

        if self.hold is not None:
            await self.hold.wait()

        if len(self.answers) > 1:
            return self.answers.pop(0)

        return self.answers[0]


def build_shard(
    monkeypatch: pytest.MonkeyPatch,
    answers: list[list[tuple[bytes, int]]],
) -> tuple[channels.ConfirmingShardConnection, FakePubSub, FakeInstance]:
    """Build a shard connection wired to fakes instead of a socket.

    Pre-populates the client, handle, and receive task so the connection believes it is already
    established, which keeps the test inside the unit layer's no-network rule.

    Arguments:
        monkeypatch: Fixture used to substitute the connection's client and handle.
        answers: Subscriber-count replies the instance should give.

    Returns:
        The shard connection, the handle it writes to, and the instance it asks.
    """
    shard = channels.ConfirmingShardConnection(HOST, FakeLayer())
    handle = FakePubSub()
    instance = FakeInstance(answers)
    monkeypatch.setattr(shard, "_redis", instance)
    monkeypatch.setattr(shard, "_pubsub", handle)
    monkeypatch.setattr(shard, "_receive_task", object())

    return shard, handle, instance


@pytest.mark.unit
@pytest.mark.asyncio
async def test_subscribing_waits_until_the_instance_reports_the_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hold the subscribe open until the instance has registered it.

    Confirms the call polls past an instance that has not yet registered the probe, which is what
    turns a join into a barrier a publish cannot overtake.

    Arguments:
        monkeypatch: Fixture used to substitute the connection's client and handle.

    Returns:
        None.

    Raises:
        AssertionError: If the call returns without the instance confirming.
    """
    shard, handle, instance = build_shard(monkeypatch, [[(b"probe", 0)], [(b"probe", 1)]])

    await shard.subscribe("localforge__group__notifications")

    assert handle.subscribed[0] == "localforge__group__notifications"
    assert handle.subscribed[1].startswith(f"{PREFIX}{channels.CONFIRMATION_CHANNEL_INFIX}")
    assert instance.queried == [handle.subscribed[1], handle.subscribed[1]]
    assert handle.unsubscribed == [handle.subscribed[1]]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_subscription_the_instance_never_reports_is_withdrawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse to pretend an unregistered subscription is live.

    Confirms the layer raises once the budget is spent and takes the subscription back off the
    instance, because returning would hand a consumer a membership that silently drops messages
    and leaving it would strand a subscription nothing tracks.

    Arguments:
        monkeypatch: Fixture used to substitute the client and shorten the confirmation budget.

    Returns:
        None.

    Raises:
        AssertionError: If no failure is raised or the subscription is left behind.
    """
    monkeypatch.setattr(channels, "CONFIRMATION_TIMEOUT_SECONDS", 0.0)

    shard, handle, _ = build_shard(monkeypatch, [[]])

    with pytest.raises(channels.SubscriptionNotConfirmedError):
        await shard.subscribe("localforge__group__silent")

    assert "localforge__group__silent" in handle.unsubscribed
    assert "localforge__group__silent" not in shard._subscribed_to  # noqa: SLF001


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_withdrawal_that_cannot_be_sent_still_forgets_the_subscription(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep the confirmation's outcome when the subscription cannot be taken back.

    Confirms a connection that has gone away is logged rather than raised over the confirmation
    failure, because replacing the reason a join failed with the reason the tidy-up failed would
    hide the defect that matters.

    Arguments:
        monkeypatch: Fixture used to substitute the client and shorten the confirmation budget.
        caplog: Fixture capturing the log record the failed withdrawal emits.

    Returns:
        None.

    Raises:
        AssertionError: If the cleanup failure escapes or goes unlogged.
    """
    monkeypatch.setattr(channels, "CONFIRMATION_TIMEOUT_SECONDS", 0.0)

    shard, handle, _ = build_shard(monkeypatch, [[]])
    handle.fail_unsubscribe = True

    with pytest.raises(channels.SubscriptionNotConfirmedError):
        await shard.subscribe("localforge__group__untidy")

    assert "could not withdraw the unconfirmed subscription" in caplog.text
    assert "could not unsubscribe the subscription probe" in caplog.text
    assert "localforge__group__untidy" not in shard._subscribed_to  # noqa: SLF001


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_subscription_already_confirmed_is_not_confirmed_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pay the confirmation cost once per subscription.

    Confirms a repeated join of the same group asks the instance nothing further, because the
    subscription is already registered and there is nothing left to wait for.

    Arguments:
        monkeypatch: Fixture used to substitute the connection's client and handle.

    Returns:
        None.

    Raises:
        AssertionError: If the repeat queries the instance again.
    """
    shard, _, instance = build_shard(monkeypatch, [[(b"probe", 1)]])

    await shard.subscribe("localforge__group__repeat")

    queried_once = len(instance.queried)

    await shard.subscribe("localforge__group__repeat")

    assert len(instance.queried) == queried_once


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_second_caller_waits_for_the_subscription_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hold a concurrent joiner until the first subscription is confirmed.

    Confirms a second caller for the same group does not return while the first is still waiting,
    because the base class records a channel as subscribed the moment the command is written and
    returning on that record is how a socket would join a group the instance has not registered.

    Arguments:
        monkeypatch: Fixture used to substitute the connection's client and handle.

    Returns:
        None.

    Raises:
        AssertionError: If the second caller returns before the first is confirmed.
    """
    shard, _, instance = build_shard(monkeypatch, [[(b"probe", 1)]])
    instance.hold = asyncio.Event()

    owner = asyncio.create_task(shard.subscribe("localforge__group__shared"))
    await asyncio.sleep(0)
    joiner = asyncio.create_task(shard.subscribe("localforge__group__shared"))
    await asyncio.sleep(0)

    assert not owner.done()
    assert not joiner.done()

    instance.hold.set()
    await asyncio.gather(owner, joiner)

    assert len(set(instance.queried)) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_group_rejoined_while_emptying_keeps_its_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse to unsubscribe a group somebody has just rejoined.

    Confirms a departure decided while the group was empty does not strip the subscription out
    from under a member who joined in the meantime, which would leave that member subscribed
    locally and absent on the instance.

    Arguments:
        monkeypatch: Fixture used to substitute the connection's client and handle.

    Returns:
        None.

    Raises:
        AssertionError: If the rejoined group loses its subscription.
    """
    shard, handle, _ = build_shard(monkeypatch, [[(b"probe", 1)]])
    group_channel = "localforge__group__churn"

    await shard.subscribe(group_channel)

    shard.channel_layer.groups[group_channel] = {"localforgespecific.rejoined"}

    await shard.unsubscribe(group_channel)

    assert group_channel not in handle.unsubscribed
    assert group_channel in shard._subscribed_to  # noqa: SLF001


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_group_nobody_rejoined_is_unsubscribed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Release a subscription the layer no longer has a member for.

    Confirms the membership re-read does not turn every departure into a leak, because a group
    that really is empty must stop carrying traffic to this process.

    Arguments:
        monkeypatch: Fixture used to substitute the connection's client and handle.

    Returns:
        None.

    Raises:
        AssertionError: If the empty group keeps its subscription.
    """
    shard, handle, _ = build_shard(monkeypatch, [[(b"probe", 1)]])
    group_channel = "localforge__group__departed"

    await shard.subscribe(group_channel)
    await shard.unsubscribe(group_channel)

    assert group_channel in handle.unsubscribed
    assert group_channel not in shard._subscribed_to  # noqa: SLF001


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_reset_waits_for_a_subscription_change_to_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep a reset out of the middle of a subscribe.

    Confirms the reset takes the same lock the subscribe holds, because landing between a
    subscribe and its confirmation would build a new connection and let the confirmation prove a
    probe on a connection that never carried the subscription.

    Arguments:
        monkeypatch: Fixture used to substitute the connection's client and handle.

    Returns:
        None.

    Raises:
        AssertionError: If the reset proceeds while a subscribe is in flight.
    """
    shard, _, instance = build_shard(monkeypatch, [[(b"probe", 1)]])
    instance.hold = asyncio.Event()

    subscribing = asyncio.create_task(shard.subscribe("localforge__group__resetting"))
    await asyncio.sleep(0)

    monkeypatch.setattr(shard, "_receive_task", None)
    monkeypatch.setattr(shard, "_redis", None)

    resetting = asyncio.create_task(shard.flush())
    await asyncio.sleep(0)

    assert not resetting.done()

    instance.hold.set()
    await asyncio.gather(subscribing, resetting)

    assert shard._subscribed_to == set()  # noqa: SLF001


@pytest.mark.unit
def test_the_loop_layer_replaces_every_shard_with_a_confirming_one() -> None:
    """Install the confirmation on every configured host.

    Confirms each shard the base class built is replaced while keeping its address, so a clustered
    host list gains the barrier on every node rather than only the first.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any shard is left unconfirmed or loses its address.
    """
    layer = channels.ConfirmingLoopLayer(
        hosts=[HOST, {"host": "second", "port": 6380}],
        prefix=PREFIX,
    )
    shards = layer._shards  # noqa: SLF001

    assert [type(shard) for shard in shards] == [channels.ConfirmingShardConnection] * 2
    assert [shard.host["host"] for shard in shards] == ["valkey-channels-vh8dm", "second"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_each_event_loop_gets_one_confirming_layer() -> None:
    """Serve one layer per event loop and reuse it.

    Confirms the substitution survives the caching the base class relies on, so a second call on
    the same loop returns the same confirming layer rather than building a plain one.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the layer is not confirming or is rebuilt on each call.
    """
    backend = channels.ConfirmedRedisPubSubChannelLayer(hosts=[HOST], prefix=PREFIX)

    first = backend._get_layer()  # noqa: SLF001
    second = backend._get_layer()  # noqa: SLF001

    assert isinstance(first, channels.ConfirmingLoopLayer)
    assert first is second
