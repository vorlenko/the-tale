# coding: utf-8
from game.quests.quests_generator.quest_line import Quest, Line, ACTOR_TYPE, DEFAULT_RESULTS
from game.quests.quests_generator import commands as cmd


class Hunt(Quest):

    SPECIAL = True

    ACTORS = [(u'место охоты', 'place_end', ACTOR_TYPE.PLACE)]

    @classmethod
    def can_be_used(cls, env):
        return env.knowlege_base.get_special('hero_pref_mob') is not None

    def initialize(self, identifier, env, place_start=None, place_end=None):
        super(Hunt, self).initialize(identifier, env)

        mob = env.knowlege_base.get_special('hero_pref_mob')

        self.env_local.register('place_start', place_start or env.new_place())
        self.env_local.register('place_end', place_end or env.new_place(terrain=mob['terrain']))


    def create_line(self, env):
        mob = env.knowlege_base.get_special('hero_pref_mob')

        sequence = [cmd.Message(event='intro'),
                    cmd.Move(place=self.env_local.place_end, event='move_to_quest') ]

        sequence += [ cmd.MoveNear(place=self.env_local.place_end, back=False, event='track'),
                      cmd.Battle(number=1, event='hunt', mob_id=mob['id'])] * 10

        sequence += [ cmd.MoveNear(place=self.env_local.place_end, back=True, event='move_to_city'),
                      cmd.QuestResult(result=DEFAULT_RESULTS.POSITIVE),
                      cmd.GetReward(person=None, event='get_reward') ]

        main_line = Line(sequence=sequence)

        self.line = env.new_line(main_line)
