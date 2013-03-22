# coding: utf-8

from collections import defaultdict

from dext.utils import s11n

import deworld

from deworld.layers import VEGETATION_TYPE

from common.utils.prototypes import BasePrototype
from common.utils.decorators import lazy_property

from game.balance.enums import RACE

from game.persons.models import PERSON_STATE
from game.persons.storage import persons_storage

from game.map.places.models import Place
from game.map.places.prototypes import PlacePrototype
from game.map.places.storage import places_storage

from game.map.models import MapInfo, WorldInfo
from game.map.utils import get_race_percents
from game.map.relations import MAP_STATISTICS



class MapInfoPrototype(BasePrototype):
    _model_class = MapInfo
    _readonly = ('id', 'turn_number', 'world_id')

    @lazy_property
    def terrain(self): return s11n.from_json(self._model.terrain)

    @lazy_property
    def statistics(self):
        statistics = s11n.from_json(self._model.statistics)
        statistics['race_percents'] = dict( (int(key), value) for key, value in statistics['race_percents'].items())
        statistics['race_cities'] = dict( (int(key), value) for key, value in statistics['race_cities'].items())
        statistics['terrain_percents'] = dict( (int(key), value) for key, value in statistics['terrain_percents'].items())
        return statistics

    @lazy_property
    def cells(self):
        from game.map.generator.descriptors import UICells
        return UICells.deserialize(s11n.from_json(self._model.cells))

    @property
    def terrain_percents(self): return self.statistics['terrain_percents']

    @property
    def race_percents(self): return self.statistics['race_percents']

    @property
    def race_cities(self): return self.statistics['race_cities']

    def get_dominant_place(self, x, y):
        for place in places_storage.all():
            if (x, y) in place.nearest_cells:
                return place
        return None

    ######################
    # object operations
    ######################

    @classmethod
    def remove_old_infos(cls):
        map_info_ids, world_info_ids =  zip(*MapInfo.objects.order_by('-created_at', '-turn_number')[:2].values_list('id', 'world_id'))
        MapInfo.objects.exclude(id__in=map_info_ids).delete()
        WorldInfo.objects.exclude(id__in=world_info_ids).delete()

    @classmethod
    def create(cls, turn_number, width, height, terrain, world):
        from game.map.generator.descriptors import UICells

        terrain_percents = {}

        terrain_squares = defaultdict(int)

        for y in xrange(0, height):
            for x in xrange(0, width):
                cell = world.generator.cell_info(x, y)

                if cell.height < -0.2:
                    terrain_squares[MAP_STATISTICS.LOWLANDS] += 1
                elif cell.height < 0.3:
                    terrain_squares[MAP_STATISTICS.PLAINS] += 1
                else:
                    terrain_squares[MAP_STATISTICS.UPLANDS] += 1

                if cell.vegetation == VEGETATION_TYPE.DESERT:
                    terrain_squares[MAP_STATISTICS.DESERTS] += 1
                elif cell.vegetation == VEGETATION_TYPE.GRASS:
                    terrain_squares[MAP_STATISTICS.GRASS] += 1
                else:
                    terrain_squares[MAP_STATISTICS.FORESTS] += 1

        total_cells = width * height

        terrain_percents = dict( (id_, float(square) / total_cells) for id_, square in terrain_squares.items())

        race_percents = get_race_percents(persons_storage.filter(state=PERSON_STATE.IN_GAME))

        #race to cities percents
        race_cities = dict( (race_id, 0) for race_id in RACE._ALL)
        for place_model in Place.objects.all():
            place = PlacePrototype(place_model)
            race_cities[place.race.value] += 1


        statistics = {'terrain_percents': terrain_percents,
                      'race_percents': race_percents,
                      'race_cities': race_cities}

        model = MapInfo.objects.create(turn_number=turn_number,
                                       width=width,
                                       height=height,
                                       terrain=s11n.to_json(terrain),
                                       cells=s11n.to_json(UICells.create(world.generator).serialize()),
                                       world=world._model,
                                       statistics=s11n.to_json(statistics))
        return cls(model)



class WorldInfoPrototype(BasePrototype):
    _model_class = WorldInfo
    _readonly = ('id',)
    _get_by = ('id', )

    @lazy_property
    def generator(self):
        world_data = s11n.from_json(self._model.data)
        return deworld.World.deserialize(config=deworld.BaseConfig, data=world_data)

    @classmethod
    def create(cls, w, h):
        generator = deworld.World(w=w, h=h, config=deworld.BaseConfig)

        model = WorldInfo.objects.create(data=s11n.to_json(generator.serialize()))

        return cls(model)

    @classmethod
    def create_from_generator(cls, generator):
        model = WorldInfo.objects.create(data=s11n.to_json(generator.serialize()))
        return cls(model)
