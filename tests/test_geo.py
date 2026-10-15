from datetime import datetime,timedelta,timezone
import unittest
from geo import distance_m,gps_motion,path_remaining,estimate_arrival


class GPSArrivalTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime.now(timezone.utc)
        self.geometry={'geometry':{'type':'LineString','coordinates':[[-72.34,-39.64],[-72.32,-39.64],[-72.30,-39.64]]}}
        self.row={'latitud':-39.64,'longitud':-72.32,'precision_m':10,'ubicacion_en':self.now,
                  'velocidad_mps':10,'velocidad_en':self.now,'muestras_movimiento':3}

    def test_projection_uses_remaining_road_instead_of_straight_line_to_start(self):
        remaining,off=path_remaining(self.geometry['geometry']['coordinates'],-39.64,-72.32)
        self.assertAlmostEqual(remaining,distance_m(-39.64,-72.32,-39.64,-72.30),delta=1)
        self.assertLess(off,1)
        estimate=estimate_arrival(self.row,self.geometry,self.now)
        self.assertEqual(estimate['eta_estado'],'disponible')
        self.assertGreater(estimate['eta_minutos'],0)

    def test_missing_stale_poor_or_off_route_gps_never_invents_arrival(self):
        for changes in ({'ubicacion_en':self.now-timedelta(minutes=3)},{'precision_m':500},
                        {'latitud':-40},{'muestras_movimiento':0},{'velocidad_mps':0}):
            with self.subTest(changes=changes):
                result=estimate_arrival({**self.row,**changes},self.geometry,self.now)
                self.assertIsNone(result['eta_minutos']);self.assertIsNone(result['llegada_estimada_en'])

    def test_jitter_gaps_and_impossible_jumps_do_not_accumulate_distance(self):
        old={**self.row,'ubicacion_en':self.now-timedelta(seconds=15),'distancia_m':100}
        jitter=gps_motion(old,-39.64,-72.32,10,self.now)
        self.assertEqual(jitter['distancia_m'],100)
        jump=gps_motion(old,-38,-70,10,self.now)
        self.assertEqual(jump['distancia_m'],100);self.assertIsNone(jump['velocidad_mps'])
        for seconds,accuracy in ((4,10),(10,101)):
            fast={**old,'ubicacion_en':self.now-timedelta(seconds=seconds)}
            self.assertTrue(gps_motion(fast,-38,-70,accuracy,self.now)['salto_imposible'])
        gap=gps_motion({**old,'ubicacion_en':self.now-timedelta(minutes=4)},-39.64,-72.31,10,self.now)
        self.assertEqual(gap['distancia_m'],100);self.assertIsNone(gap['velocidad_mps'])

    def test_real_movement_updates_distance_and_speed_without_retaining_history(self):
        old={**self.row,'ubicacion_en':self.now-timedelta(seconds=15),'distancia_m':0,'velocidad_mps':None}
        result=gps_motion(old,-39.64,-72.318,10,self.now)
        self.assertGreater(result['distancia_m'],100);self.assertGreater(result['velocidad_mps'],1)
        self.assertNotIn('latitud',result);self.assertEqual(result['muestras_movimiento'],4)
