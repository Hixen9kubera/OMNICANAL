"""
attribute_mapper.py — Mapea meta_data de WooCommerce a atributos de MercadoLibre

Los atributos en WooCommerce vienen como:
  ml_attr_brand   → marca
  ml_attr_model   → modelo
  ml_attr_color   → color
  etc.

Este módulo consulta los atributos requeridos de la categoría ML
y construye la lista de atributos para el payload del item.
"""
from __future__ import annotations

# Mapeo de nombres de atributos WooCommerce (español, lowercase) → IDs de ML
# Cubre los ~612 IDs únicos recolectados de 54 categorías ML México.
# Regla: una clave por variante ortográfica (con/sin tilde, con/sin artículo).
WC_TO_ML_ID: dict[str, str] = {

    # ── Alimentación / fuente de energía ──────────────────────────────────────
    'fuente de alimentacion':               'POWER_SOURCE_TYPE',
    'fuente de alimentación':               'POWER_SOURCE_TYPE',
    'tipo de alimentacion':                 'POWER_SOURCE_TYPE',
    'tipo de alimentación':                 'POWER_SOURCE_TYPE',
    'fuente de poder':                      'POWER_SOURCE_TYPE',
    'tipo de fuente':                       'POWER_SOURCE_TYPE',
    'fuente de energia':                    'POWER_SOURCE_TYPE',   # 23 usos en WC
    'fuente de energía':                    'POWER_SOURCE_TYPE',
    # Algunas categorías usan POWER_SUPPLY_TYPE (Tipo de alimentación)
    'suministro de energia':                'POWER_SUPPLY_TYPE',
    'suministro de energía':                'POWER_SUPPLY_TYPE',
    'tipo de suministro':                   'POWER_SUPPLY_TYPE',
    'alimentacion':                         'POWER_SUPPLY_TYPE',
    'alimentación':                         'POWER_SUPPLY_TYPE',
    'tipos de alimentacion':                'POWER_SUPPLY_TYPES',
    'tipos de alimentación':                'POWER_SUPPLY_TYPES',

    # ── Voltaje / tensión (general) ───────────────────────────────────────────
    'voltaje':                              'VOLTAGE',
    'voltaje nominal':                      'RATED_VOLTAGE',
    'voltaje de funcionamiento':            'OPERATING_VOLTAGE',
    'voltaje de entrada':                   'INPUT_VOLTAGE',
    'voltaje de salida':                    ['OUTPUT_VOLTAGE', 'BATTERY_VOLTAGE'],
    'tension':                              'VOLTAGE',
    'tensión':                              'VOLTAGE',
    'tension nominal':                      'RATED_VOLTAGE',
    'tensión nominal':                      'RATED_VOLTAGE',

    # ── Batería ───────────────────────────────────────────────────────────────
    'tipo de bateria':                      'BATTERY_TYPE',
    'tipo de batería':                      'BATTERY_TYPE',
    'tipo de bateria compatible':           'BATTERY_TYPE',
    'tipo de batería compatible':           'BATTERY_TYPE',
    'bateria compatible':                   'BATTERY_TYPE',
    'batería compatible':                   'BATTERY_TYPE',
    'bateria':                              'BATTERY_TYPE',
    'batería':                              'BATTERY_TYPE',
    'voltaje de la bateria':                'BATTERY_VOLTAGE',
    'voltaje de la batería':                'BATTERY_VOLTAGE',
    'voltaje de bateria':                   'BATTERY_VOLTAGE',
    'voltaje de batería':                   'BATTERY_VOLTAGE',
    'capacidad de bateria':                 'BATTERY_CAPACITY',
    'capacidad de batería':                 'BATTERY_CAPACITY',
    'capacidad de la bateria':              'BATTERY_CAPACITY',
    'capacidad de la batería':              'BATTERY_CAPACITY',
    'tiempo de carga':                      'CHARGE_TIME',
    'tiempo de recarga':                    'CHARGE_TIME',
    'tiempo de trabajo continuo':           'OPERATING_TIME',
    'autonomia':                            'OPERATING_TIME',
    'autonomía':                            'OPERATING_TIME',
    'duracion de bateria':                  'OPERATING_TIME',
    'duración de batería':                  'OPERATING_TIME',
    'vida de la bateria':                   'BATTERY_LIFE',
    'vida de la batería':                   'BATTERY_LIFE',
    'celdas de bateria':                    'BATTERY_CELLS',
    'celdas de batería':                    'BATTERY_CELLS',
    'cantidad de baterias':                 'BATTERY_QUANTITY',
    'cantidad de baterías':                 'BATTERY_QUANTITY',
    'incluye bateria':                      'INCLUDES_BATTERY',
    'incluye batería':                      'INCLUDES_BATTERY',

    # ── Potencia / consumo ────────────────────────────────────────────────────
    'potencia':                             'POWER',
    'potencia nominal':                     'RATED_POWER',
    'potencia maxima':                      'MAX_POWER',
    'potencia máxima':                      'MAX_POWER',
    'potencia del motor':                   'ENGINE_POWER',
    'potencia nominal del motor':           'RATED_MOTOR_POWER',
    'potencia de entrada':                  'INPUT_POWER',
    'potencia de salida':                   'OUTPUT_POWER',
    'watts':                                'WATTAGE',
    'vatios':                               'WATTAGE',
    'consumo':                              'CONSUMPTION',
    'consumo energetico':                   'POWER_CONSUMPTION',
    'consumo energético':                   'POWER_CONSUMPTION',
    'amperaje':                             'AMPERAGE',
    'corriente':                            'AMPERAGE',              # 4 usos en WC
    'corriente de salida':                  'OUTPUT_CURRENT',        # 5 usos en WC
    'corriente maxima':                     'MAX_CURRENT',
    'corriente máxima':                     'MAX_CURRENT',
    'amperaje del motor':                   'MOTOR_AMPERAGE',
    'amperaje maximo':                      'MAX_AMPERAGE',
    'amperaje máximo':                      'MAX_AMPERAGE',

    # ── Características generales ─────────────────────────────────────────────
    'caracteristicas':                      'FEATURES',             # 67 usos en WC
    'características':                      'FEATURES',
    'caracteristica':                       'FEATURES',             # 37 usos en WC
    'característica':                       'FEATURES',
    'caracteristicas adicionales':          'ADDITIONAL_FEATURES',
    'características adicionales':          'ADDITIONAL_FEATURES',
    'caracteristica adicional':             'ADDITIONAL_FEATURES',
    'característica adicional':             'ADDITIONAL_FEATURES',
    'caracteristica especial':              'FEATURES',
    'característica especial':              'FEATURES',
    'caracteristicas especiales':           'FEATURES',
    'características especiales':           'FEATURES',
    'caracteristica principal':             'FEATURES',
    'característica principal':             'FEATURES',
    'caracteristicas tecnicas':             'FEATURES',
    'características técnicas':             'FEATURES',

    # ── Género / estilo / forma ───────────────────────────────────────────────
    'genero':                               'GENDER',               # 114 usos en WC
    'género':                               'GENDER',
    'estilo':                               'STYLE',                # 100 usos en WC
    'estilos':                              'STYLES',
    'tipo':                                 'PRODUCT_TYPE',         # 90 usos (genérico)
    'tipo de producto':                     'PRODUCT_TYPE',         # 61 usos en WC
    'tipo de vehiculo':                     'VEHICLE_TYPE',         # 48 usos en WC
    'tipo de vehículo':                     'VEHICLE_TYPE',
    'clasificacion del vehiculo':           'VEHICLE_CLASSIFICATION',
    'clasificación del vehículo':           'VEHICLE_CLASSIFICATION',
    'funcion':                              'FUNCTION',             # 40 usos en WC
    'función':                              'FUNCTION',
    'funciones':                            'FUNCTIONS',            # 20 usos en WC
    'funciones adicionales':                'ADDITIONAL_FUNCTIONS',
    'forma':                                'SHAPE',                # 38 usos en WC
    'tipo de cierre':                       'CLOSURE_TYPE',         # 17 usos en WC
    'tipos de cierre':                      'CLOSURE_TYPES',
    'cierre':                               'CLOSURE_TYPE',
    'tipo de tela':                         'FABRIC_TYPE',          # 10 usos en WC
    'tipo de ajuste':                       'ADJUSTMENT_TYPE',
    'tipos de ajuste':                      'ADJUSTMENT_TYPES',
    'formato de venta':                     'SALE_FORMAT',
    'temporada':                            'SEASON',
    'edad minima recomendada':              'MIN_RECOMMENDED_AGE',
    'edad mínima recomendada':              'MIN_RECOMMENDED_AGE',
    'edad maxima recomendada':              'MAX_RECOMMENDED_AGE',
    'edad máxima recomendada':              'MAX_RECOMMENDED_AGE',
    'edad recomendada':                     'RECOMMENDED_AGE_GROUP',
    'grupo de edad':                        'AGE_GROUP',
    'rango de edad':                        'AGE_PERIOD',

    # ── Motor / mecánica ──────────────────────────────────────────────────────
    'tipo de motor':                        'MOTOR_TYPE',
    'motor':                                'MOTOR_TYPE',
    # ENGINE_TYPE = Tipo de motor (motores de combustión/jardín)
    'tipo de motor de combustion':          'ENGINE_TYPE',
    'tipo de motor de combustión':          'ENGINE_TYPE',
    'tipo de motor a gasolina':             'ENGINE_TYPE',
    'velocidad maxima':                     'MAX_SPEED',
    'velocidad máxima':                     'MAX_SPEED',             # 6 usos en WC
    'velocidad de giro':                    'MAX_SPEED',
    'velocidad sin carga':                  'NO_LOAD_SPEED',         # 8 usos en WC
    'velocidad en vacio':                   'NO_LOAD_SPEED',         # 8 usos en WC
    'velocidad en vacío':                   'NO_LOAD_SPEED',
    'velocidades':                          'SPEEDS',
    'cantidad de velocidades':              'SPEEDS_NUMBER',         # 10 usos en WC
    'velocidad del motor':                  'ENGINE_SPEED',
    'velocidad maxima de rotacion':         'MAX_ROTATION_SPEED',
    'velocidad máxima de rotación':         'MAX_ROTATION_SPEED',
    'rpm':                                  'RPM',
    'rpm maximas':                          'MAX_RPM',
    'rpm máximas':                          'MAX_RPM',
    'torque maximo':                        'MAX_TORQUE',
    'torque máximo':                        'MAX_TORQUE',
    'par maximo':                           'MAX_TORQUE',
    'par máximo':                           'MAX_TORQUE',
    'numero de velocidades':                'NUMBER_OF_SPEEDS',
    'número de velocidades':                'NUMBER_OF_SPEEDS',
    'velocidades':                          'NUMBER_OF_SPEEDS',
    'tipo de transmision':                  'TRANSMISSION_TYPE',
    'tipo de transmisión':                  'TRANSMISSION_TYPE',
    'cilindrada':                           'DISPLACEMENT',
    'desplazamiento':                       'DISPLACEMENT',
    'cilindros':                            'CYLINDERS_NUMBER',

    # ── Herramientas de corte / taladro ───────────────────────────────────────
    'tamano del mandril':                   'MANDREL_SIZE',
    'tamaño del mandril':                   'MANDREL_SIZE',
    'tamano de mandril':                    'MANDREL_SIZE',
    'tamaño de mandril':                    'MANDREL_SIZE',
    'capacidad del mandril':                'MANDREL_SIZE',
    'tipo de mandril':                      'CHUCK_TYPE',
    'tipo de portabroca':                   'DRILL_CHUCK_TYPE',
    'tipo de portabrocas':                  'DRILL_CHUCK_TYPE',
    'diametro de disco':                    'DISC_DIAMETER',
    'diámetro de disco':                    'DISC_DIAMETER',
    'diametro del disco':                   'DISC_DIAMETER',
    'diámetro del disco':                   'DISC_DIAMETER',
    'tipo de disco':                        'DISC_TYPE',
    'tamaño del disco':                     'DISC_SIZE',
    'tamano del disco':                     'DISC_SIZE',
    'profundidad de corte':                 'CUTTING_DEPTH_90_DEGREES',
    'profundidad de corte 90':              'CUTTING_DEPTH_90_DEGREES',
    'profundidad de corte 45':              'CUTTING_DEPTH_45_DEGREES',
    'angulo de corte':                      'CUTTING_ANGLE',
    'ángulo de corte':                      'CUTTING_ANGLE',
    'capacidad de corte':                   'CUTTING_CAPACITY',
    'ancho de corte':                       'CUTTING_WIDTH',
    'largo de corte':                       'CUT_LENGTH',
    'tipo de sierra':                       'SAW_TYPE',
    'tipo de hoja':                         'BLADE_TYPE',
    'tipo de cuchilla':                     'BLADE_TYPE',
    'largo de la hoja':                     'BLADE_LENGTH',
    'largo de hoja':                        'BLADE_LENGHT',
    'material de la hoja':                  'BLADE_MATERIAL',
    'hoja incluida':                        'INCLUDES_BLADE',
    'movimientos por minuto':               'STROKES_PER_MINUTE',
    'spm':                                  'STROKES_PER_MINUTE',
    'impactos por minuto':                  'IMPACTS_PER_MINUTE',
    'ipm':                                  'IMPACTS_PER_MINUTE',
    'golpes por minuto':                    'BEATS_PER_MINUTE',
    'tipo de broca':                        'DRILL_BIT_TYPE',
    'tamano de broca':                      'DRILL_BIT_SIZE',
    'tamaño de broca':                      'DRILL_BIT_SIZE',
    'tipo de esmeriladora':                 'POWER_GRINDER_TYPE',
    'tipo de destornillador':               'ELECTRIC_SCREWDRIVER_TYPE',

    # ── Hidráulica / fluidos / limpieza ──────────────────────────────────────
    'flujo maximo':                         'MAX_FLOW',
    'flujo máximo':                         'MAX_FLOW',
    'caudal maximo':                        'MAX_FLOW',
    'caudal máximo':                        'MAX_FLOW',
    'caudal de agua':                       'WATER_FLOW',
    'flujo de agua':                        'WATER_FLOW',
    'caudal maximo de agua':                'MAX_WATER_FLOW',
    'caudal máximo de agua':                'MAX_WATER_FLOW',
    'caudal de aire':                       'AIRFLOW',
    'flujo de aire':                        'AIR_FLOW',
    'presion maxima':                       'MAX_PRESSURE',
    'presión máxima':                       'MAX_PRESSURE',
    'presion de trabajo':                   'WORKING_PRESSURE',
    'presión de trabajo':                   'WORKING_PRESSURE',
    'presion de succion':                   'SUCTION_PRESSURE',
    'presión de succión':                   'SUCTION_PRESSURE',
    'presion':                              'PRESSURE',
    'presión':                              'PRESSURE',
    'potencia de succion':                  'SUCTION_POWER',
    'potencia de succión':                  'SUCTION_POWER',
    'longitud de manguera de agua':         'HOSE_LENGTH',
    'longitud de manguera':                 'HOSE_LENGTH',
    'largo de manguera':                    'HOSE_LENGTH',
    'largo de la manguera':                 'HOSE_LENGTH',
    'longitud del cable':                   'CABLE_LENGTH',
    'largo del cable':                      'CABLE_LENGTH',
    'longitud de cable':                    'CABLE_LENGTH',
    'largo del cable electrico':            'ELECTRIC_CABLE_LENGTH',
    'largo del cable eléctrico':            'ELECTRIC_CABLE_LENGTH',
    'largo del cable de poder':             'POWER_CORD_LENGTH',
    'tipo de boquilla':                     'NOZZLE_TYPE',
    'diametro de la boquilla':              'NOZZLE_DIAMETER',
    'diámetro de la boquilla':              'NOZZLE_DIAMETER',
    'material de la boquilla':              'NOZZLE_MATERIAL',
    'largo de la lanza':                    'LANCE_LENGTH',
    'longitud de la lanza':                 'LANCE_LENGTH',
    'material de la lanza':                 'LANCE_MATERIAL',
    'diametro de entrada':                  'INLET_DIAMETER',
    'diámetro de entrada':                  'INLET_DIAMETER',
    'diametro de salida':                   'OUTLET_DIAMETER',
    'diámetro de salida':                   'OUTLET_DIAMETER',
    'temperatura maxima del agua':          'MAX_WATER_TEMPERATURE',
    'temperatura máxima del agua':          'MAX_WATER_TEMPERATURE',
    'tipo de bomba':                        'PUMP_TYPE',
    'material de la bomba':                 'PUMP_MATERIAL',

    # ── Compresor / aire ──────────────────────────────────────────────────────
    'capacidad del tanque':                 'TANK_CAPACITY',
    'capacidad de tanque':                  'TANK_CAPACITY',
    'volumen del tanque':                   'TANK_VOLUME',
    'capacidad del tanque de agua':         'WATER_TANK_CAPACITY',
    'capacidad del tanque de combustible':  'FUEL_TANK_CAPACITY',
    'material del tanque':                  'TANK_MATERIAL',
    'tipo de compresor':                    'COMPRESSOR_TYPE',
    'nivel de aceite':                      'OIL_LEVEL',
    'tipo de aceite':                       'OIL_TYPE',
    'caudal de aceite':                     'OIL_FLOW',

    # ── Sonido / vibraciones ──────────────────────────────────────────────────
    'nivel de presion sonora':              'SOUND_PRESSURE_LEVEL',
    'nivel de presión sonora':              'SOUND_PRESSURE_LEVEL',
    'nivel de ruido':                       'NOISE_LEVEL',
    'ruido':                                'NOISE_LEVEL',
    'nivel de sonido':                      'SOUND_LEVEL',
    'decibeles':                            'SOUND_PRESSURE_LEVEL',
    'db':                                   'SOUND_PRESSURE_LEVEL',
    'vibracion':                            'VIBRATION',
    'vibración':                            'VIBRATION',
    'nivel de vibracion':                   'VIBRATION_LEVEL',
    'nivel de vibración':                   'VIBRATION_LEVEL',

    # ── Temperatura ───────────────────────────────────────────────────────────
    'temperatura maxima':                   'MAX_TEMPERATURE',
    'temperatura máxima':                   'MAX_TEMPERATURE',
    'temperatura minima':                   'MIN_TEMPERATURE',
    'temperatura mínima':                   'MIN_TEMPERATURE',
    'temperatura de trabajo':               'WORKING_TEMPERATURE',
    'temperatura de operacion':             'OPERATING_TEMPERATURE',
    'temperatura de operación':             'OPERATING_TEMPERATURE',
    'rango de temperatura':                 'TEMPERATURE_RANGE',
    'tipo de calefaccion':                  'HEATING_TYPE',
    'tipo de calefacción':                  'HEATING_TYPE',
    'potencia de calefaccion':              'HEATING_POWER',
    'potencia de calefacción':              'HEATING_POWER',
    'btu':                                  'BTU',

    # ── Aplicación / uso ──────────────────────────────────────────────────────
    'aplicacion':                           'RECOMMENDED_USE',
    'aplicación':                           'RECOMMENDED_USE',
    'uso recomendado':                      'RECOMMENDED_USE',
    'uso':                                  'RECOMMENDED_USE',
    # Algunas categorías usan RECOMMENDED_USES (plural)
    'usos recomendados':                    'RECOMMENDED_USES',
    'usos':                                 'RECOMMENDED_USES',
    'aplicaciones':                         'RECOMMENDED_USES',
    'superficies compatibles':              'COMPATIBLE_SURFACES',
    'superficies de aplicacion':            'COMPATIBLE_SURFACES',
    'superficies de aplicación':            'COMPATIBLE_SURFACES',
    'tipo de superficie':                   'SURFACE_TYPE',
    'tipo de suelo':                        'FLOOR_TYPE',
    'compatibilidad':                       'COMPATIBILITY',
    'compatible con':                       'COMPATIBILITY',

    # ── Color / estética ──────────────────────────────────────────────────────
    'color':                                'COLOR',
    'color principal':                      'COLOR',
    'color predominante':                   'COLOR',
    'color de la luz':                      'LIGHT_COLOR',          # 10 usos en WC
    'color de la carcasa':                  'HOUSING_COLOR',
    'acabado':                              'FINISH',
    'tipo de acabado':                      'FINISH',
    'diseno':                               'DESIGN',
    'diseño':                               'DESIGN',               # 15 usos en WC
    'patron':                               'PATTERN_NAME',
    'patrón':                               'PATTERN_NAME',
    'nombre del diseno':                    'PATTERN_NAME',
    'nombre del diseño':                    'PATTERN_NAME',

    # ── Material ──────────────────────────────────────────────────────────────
    'material':                             'MATERIAL',
    'material principal':                   'MAIN_MATERIAL',
    'materiales':                           'MATERIALS',             # 19 usos en WC
    'material del cuerpo':                  'BODY_MATERIAL',
    'materiales del cuerpo':                'BODY_MATERIALS',
    'material de la carcasa':               'HOUSING_MATERIAL',      # 10 usos en WC
    'material del mango':                   'HANDLE_MATERIAL',
    'material del cable':                   'CORD_MATERIAL',
    'material del cepillo':                 'BRUSH_MATERIAL',
    'material del filtro':                  'FILTER_MATERIAL',
    'material de la hoja':                  'BLADE_MATERIAL',
    'material del tanque':                  'TANK_MATERIAL',
    'materiales del exterior':              'EXTERIOR_MATERIALS',    # 17 usos en WC
    'material del exterior':                'EXTERIOR_MATERIAL',
    'materiales del interior':              'INTERIOR_MATERIALS',    # 12 usos en WC
    'material del interior':                'INTERIOR_MATERIAL',
    'materiales de revestimiento':          'COATING_MATERIALS',
    'materiales de la estructura':          'STRUCTURE_MATERIALS',
    'material de la estructura':            'STRUCTURE_MATERIAL',

    # ── Dimensiones ───────────────────────────────────────────────────────────
    'largo':                                'LENGTH',
    'longitud':                             'LENGTH',
    'largo del producto':                   'LENGTH',
    'ancho':                                'WIDTH',
    'anchura':                              'WIDTH',
    'alto':                                 'HEIGHT',
    'altura':                               'HEIGHT',
    'alto del producto':                    'HEIGHT',
    'diametro':                             'DIAMETER',
    'diámetro':                             'DIAMETER',
    'diametro exterior':                    'OUTER_DIAMETER',
    'diámetro exterior':                    'OUTER_DIAMETER',
    'diametro interior':                    'INNER_DIAMETER',
    'diámetro interior':                    'INNER_DIAMETER',
    'peso':                                 'WEIGHT',
    'peso neto':                            'NET_WEIGHT',            # 8 usos en WC
    'peso del producto':                    'WEIGHT',
    'grosor':                               'THICKNESS',
    'espesor':                              'THICKNESS',
    'profundidad':                          'DEPTH',

    # ── Capacidad / tamaño ────────────────────────────────────────────────────
    'capacidad':                            'CAPACITY',
    'volumen':                              'CAPACITY',
    'capacidad en volumen':                 'VOLUME_CAPACITY',       # 13 usos en WC
    'capacidad en peso':                    'WEIGHT_CAPACITY',       # 10 usos en WC
    'capacidad maxima':                     'MAXIMUM_CAPACITY',
    'capacidad máxima':                     'MAXIMUM_CAPACITY',
    'capacidad maxima de personas':         'MAX_PEOPLE_CAPACITY',
    'capacidad máxima de personas':         'MAX_PEOPLE_CAPACITY',
    'capacidad de personas':                'MAX_PEOPLE_CAPACITY',
    'capacidad de almacenamiento':          'STORAGE_CAPACITY',
    'capacidad de llenado':                 'FILL_CAPACITY',
    'tamano':                               'SIZE',
    'tamaño':                               'SIZE',
    'talla':                                'SIZE',

    # ── Conectividad / tecnología ─────────────────────────────────────────────
    'conectividad':                         'CONNECTIVITY',
    'tipo de conexion':                     ['CONNECTOR_TYPES', 'CONNECTION_TYPE'],
    'tipo de conexión':                     ['CONNECTOR_TYPES', 'CONNECTION_TYPE'],
    'tipo de conector':                     ['CONNECTOR_TYPES', 'CONNECTION_TYPE'],
    'tipo de conectores':                   ['CONNECTOR_TYPES', 'CONNECTION_TYPE'],
    'interfaz':                             'INTERFACE',
    'protocolo':                            'PROTOCOL',
    'frecuencia':                           'FREQUENCY',
    'rango de frecuencia':                  'FREQUENCY_RANGE',
    'bluetooth':                            'CONNECTIVITY',
    'wifi':                                 'CONNECTIVITY',
    'tipo de antena':                       'ANTENNA_TYPE',
    'tipo de sensor':                       'SENSOR_TYPE',
    'resolucion':                           'RESOLUTION',
    'resolución':                           'RESOLUTION',
    'tipo de pantalla':                     'DISPLAY_TYPE',
    'tamano de pantalla':                   'DISPLAY_SIZE',
    'tamaño de pantalla':                   'DISPLAY_SIZE',

    # ── Seguridad / protección ────────────────────────────────────────────────
    'nivel de proteccion ip':               'IP_RATING',
    'nivel de protección ip':               'IP_RATING',
    'ip':                                   'IP_RATING',
    'grado de proteccion':                  'IP_RATING',
    'grado de protección':                  'IP_RATING',
    'certificacion':                        'CERTIFICATION',
    'certificación':                        'CERTIFICATION',
    'norma':                                'CERTIFICATION',
    'clase de proteccion':                  'PROTECTION_CLASS',
    'clase de protección':                  'PROTECTION_CLASS',
    'tipo de seguro':                       'LOCK_TYPE',
    'mecanismo de seguridad':               'SAFETY_MECHANISM',

    # ── Iluminación ───────────────────────────────────────────────────────────
    'tipo de lampara':                      'LAMP_TYPE',
    'tipo de lámpara':                      'LAMP_TYPE',
    'tipo de bombilla':                     'LAMP_TYPE',
    'tipo de foco':                         'LAMP_TYPE',
    'tipo de led':                          'LED_TYPE',
    'lumenes':                              'LUMENS',
    'lúmenes':                              'LUMENS',
    'flujo luminoso':                       'LUMENS',
    'temperatura de color':                 'COLOR_TEMPERATURE',
    'indice de reproduccion cromática':     'CRI',
    'cri':                                  'CRI',
    'ip iluminacion':                       'IP_RATING',
    'angulo de apertura':                   'BEAM_ANGLE',
    'ángulo de apertura':                   'BEAM_ANGLE',

    # ── Ropa / moda ───────────────────────────────────────────────────────────
    'tipo de traje de bano':                'SWIMWEAR_TYPE',        # 9 usos en WC
    'tipo de traje de baño':                'SWIMWEAR_TYPE',
    'tipo de bra':                          'BRA_TYPE',             # 7 usos en WC
    'escote':                               'NECK_TYPE',
    'tipo de escote':                       'NECK_TYPE',
    'tipo de patron':                       'FABRIC_DESIGN',        # 4 usos en WC
    'tipo de patrón':                       'FABRIC_DESIGN',
    'diseno de la tela':                    'FABRIC_DESIGN',
    'diseño de la tela':                    'FABRIC_DESIGN',
    'decoracion':                           'DESIGN',
    'decoración':                           'DESIGN',
    'longitud de manga':                    'SLEEVE_LENGTH',        # 3 usos en WC
    'largo de la manga':                    'SLEEVE_LENGTH',
    'tipo de manga':                        'SLEEVE_TYPE',
    'tipo de pantalon':                     'PANT_TYPE',
    'tipo de pantalón':                     'PANT_TYPE',
    'tipo de calzado':                      'FOOTWEAR_TYPE',
    'materiales de la suela':               'OUTSOLE_MATERIALS',
    'materiales del exterior':              'EXTERIOR_MATERIALS',
    'temporada':                            'SEASON',
    'ocasion':                              'OCCASIONS',            # 3 usos en WC
    'ocasión':                              'OCCASIONS',

    # ── Varios ────────────────────────────────────────────────────────────────
    'peso de la tela':                      'GRAMMAGE',             # 8 usos en WC
    'gramaje':                              'GRAMMAGE',
    'cantidad de unidades por set':         'UNITS_NUMBER_PER_SET', # 3 usos en WC
    'unidades por set':                     'UNITS_NUMBER_PER_SET',
    'posicion':                             'POSITION',
    'posición':                             'POSITION',
    'contenido del paquete':                'ACCESSORIES_INCLUDED',
    'capacidad de agua':                    'WATER_CAPACITY',
    'tiempo de funcionamiento':             'OPERATING_TIME',
    'tiempo de funcionamiento estimado':    'ESTIMATED_OPERATING_TIME',
    'angulo de vision':                     'VISION_ANGLE',
    'ángulo de visión':                     'VISION_ANGLE',
    'es un kit de fabrica':                 'IS_FACTORY_KIT',
    'es un kit de fábrica':                 'IS_FACTORY_KIT',
    'idioma de operacion':                  'LANGUAGE',
    'idioma de operación':                  'LANGUAGE',
    'idiomas de operacion':                 'LANGUAGE',
    'idiomas de operación':                 'LANGUAGE',
    'vida util':                            'LIFESPAN',
    'vida útil':                            'LIFESPAN',

    # ── Hogar / electrodomésticos ─────────────────────────────────────────────
    'tipo de aspiradora':                   'VACUUM_TYPE',
    'tipo de aspiracion':                   'SUCTION_TYPE',
    'tipo de aspiración':                   'SUCTION_TYPE',
    'potencia de succion':                  'SUCTION_POWER',
    'potencia de succión':                  'SUCTION_POWER',
    'capacidad de deposito':                'TANK_CAPACITY',
    'capacidad de depósito':                'TANK_CAPACITY',
    'tipo de filtro':                       'FILTER_TYPE',
    'filtro hepa':                          'FILTER_TYPE',
    'tipo de cepillo':                      'BRUSH_TYPE',
    'funcion':                              'FUNCTION',
    'función':                              'FUNCTION',
    'modo de operacion':                    'OPERATION_MODE',
    'modo de operación':                    'OPERATION_MODE',
    'control remoto':                       'INCLUDES_REMOTE',
    'incluye control remoto':               'INCLUDES_REMOTE',
    'tipo de control':                      'CONTROL_TYPE',
    'tipo de instalacion':                  'INSTALLATION_TYPE',
    'tipo de instalación':                  'INSTALLATION_TYPE',
    'forma de instalacion':                 'INSTALLATION_TYPE',
    'forma de instalación':                 'INSTALLATION_TYPE',
    'numero de etapas':                     'NUMBER_OF_STAGES',
    'número de etapas':                     'NUMBER_OF_STAGES',
    'etapas de filtracion':                 'NUMBER_OF_STAGES',
    'etapas de filtración':                 'NUMBER_OF_STAGES',

    # ── Jardín / exterior ─────────────────────────────────────────────────────
    'tipo de cortadora':                    'CUTTER_TYPE',
    'tipo de combustible':                  'FUEL_TYPE',
    'combustible':                          'FUEL_TYPE',
    'consumo de combustible':               'FUEL_CONSUMPTION',
    'capacidad del deposito de combustible':'FUEL_TANK_CAPACITY',
    'tipo de arranque':                     'IGNITION_TYPE',
    'tipo de inicio':                       'IGNITION_TYPE',
    'tipo de encendido':                    'IGNITION_TYPE',
    'sistema de encendido':                 'GRILL_IGNITION_SYSTEM',
    'tipo de gas':                          'GAS_TYPE',

    # ── Pintura / acabados ────────────────────────────────────────────────────
    'tipo de pistola':                      'GUN_TYPE',
    'capacidad del recipiente':             'CONTAINER_CAPACITY',
    'capacidad del vaso':                   'CONTAINER_CAPACITY',
    'caudal de pintura':                    'PAINT_FLOW',
    'tamano de boquilla':                   'NOZZLE_SIZE',
    'tamaño de boquilla':                   'NOZZLE_SIZE',
    'presion de trabajo de pintura':        'WORKING_PRESSURE',

    # ── Soldadura ─────────────────────────────────────────────────────────────
    'tipo de soldadora':                    'WELDER_TYPE',
    'corriente de soldadura':               'WELDING_CURRENT',
    'ciclo de trabajo':                     'DUTY_CYCLE',
    'diametro de electrodo':                'ELECTRODE_DIAMETER',
    'diámetro de electrodo':                'ELECTRODE_DIAMETER',
    'tipo de electrodo':                    'ELECTRODE_TYPE',
    'tipo de proceso':                      'WELDING_PROCESS',

    # ── Soldadura ─────────────────────────────────────────────────────────────
    'tipo de soldadora':                    'WELDER_TYPE',
    'corriente de soldadura':               'WELDING_CURRENT',
    'ciclo de trabajo':                     'WORK_CYCLES',
    'ciclos de trabajo':                    'WORK_CYCLES',
    'diametro de electrodo':                'ELECTRODE_DIAMETER',
    'diámetro de electrodo':                'ELECTRODE_DIAMETER',
    'tipo de electrodo':                    'ELECTRODE_TYPE',
    'materiales de soldado':                'WELDING_MATERIALS',

    # ── General / identificación ──────────────────────────────────────────────
    'marca':                                'BRAND',
    'fabricante':                           'BRAND',
    'modelo':                               'MODEL',
    'numero de modelo':                     'MODEL',
    'número de modelo':                     'MODEL',
    'linea':                                'LINE',
    'línea':                                'LINE',
    'serie':                                'SERIES',
    'numero de parte':                      'PART_NUMBER',
    'número de parte':                      'PART_NUMBER',
    'mpn':                                  'MPN',
    'codigo oem':                           'OEM',
    'código oem':                           'OEM',
    'referencia':                           'PART_NUMBER',
    'accesorios incluidos':                 'ACCESSORIES_INCLUDED',
    'tipo de empaque':                      'PACKAGING_TYPE',
    'tipo de envase':                       'PACKAGING_TYPE',
    'formato del envase':                   'PACKAGING_CONTAINER_FORMAT',
    'numero de piezas':                     'PIECES_NUMBER',
    'número de piezas':                     'PIECES_NUMBER',
    'cantidad de piezas':                   'PIECES_NUMBER',
    'piezas':                               'PIECES_NUMBER',
    'unidades por paquete':                 'UNITS_PER_PACKAGE',
    'unidades por pack':                    'UNITS_PER_PACK',
    'origen':                               'ORIGIN',
    'pais de origen':                       'ORIGIN',
    'país de origen':                       'ORIGIN',
    'garantia':                             'WARRANTY',
    'garantía':                             'WARRANTY',
    'tipo de garantia':                     'WARRANTY',
    'tipo de garantía':                     'WARRANTY',
    'certificacion':                        'CERTIFICATION',
    'certificación':                        'CERTIFICATION',
    'certificaciones':                      'CERTIFICATIONS',       # 24 usos en WC
    'norma':                                'CERTIFICATION',
    'clasificacion ip':                     'IP_RATING',
    'peso maximo soportado':                'MAX_WEIGHT_SUPPORTED',  # 35 usos en WC
    'peso máximo soportado':                'MAX_WEIGHT_SUPPORTED',
    'incluye manual de ensamblado':         'INCLUDES_ASSEMBLY_MANUAL', # 35 usos en WC
    'requiere ensamblado':                  'REQUIRES_ASSEMBLY',    # 32 usos en WC
    'incluye accesorios':                   'INCLUDES_ACCESSORIES', # 22 usos en WC
    'clasificación ip':                     'IP_RATING',
    'grado ip':                             'IP_RATING',
    'eficiencia energetica':                'ENERGY_EFFICIENCY_MEXICO',
    'eficiencia energética':                'ENERGY_EFFICIENCY_MEXICO',
    'clase energetica':                     'ENERGY_EFFICIENCY_MEXICO',
    'clase energética':                     'ENERGY_EFFICIENCY_MEXICO',
}


_SKIP_VALUE_TYPES = {'grid_id', 'grid_row_id', 'picture'}


def _is_numeric(val: str) -> bool:
    """True si val parece un número (entero, decimal o con fracción)."""
    import re
    if not val:
        return False
    v = val.strip().replace(',', '.')
    return bool(re.match(r'^-?\d+(\.\d+)?$', v) or re.match(r'^\d+\s*/\s*\d+$', v))


def _validate_value(attr: dict, val_str: str) -> str | None:
    """
    Valida/normaliza el value_name según el value_type del atributo.
    - number_unit → llama _format_number_unit
    - number      → solo dígitos; si no, devuelve None (omitir)
    - Edad        → agrega 'años' si es numérico
    Retorna el valor final o None si el atributo debe omitirse.
    """
    vt = attr.get('value_type')
    attr_id = attr.get('id', '')

    if vt == 'number_unit':
        return _format_number_unit(val_str, attr.get('default_unit', ''), attr.get('allowed_units'))

    if vt == 'number':
        # ML rechaza strings tipo "N/A" para atributos numéricos
        if not _is_numeric(val_str):
            return None
        return _resolve_fraction(val_str).replace(',', '.')

    if attr_id in ('MIN_RECOMMENDED_AGE', 'MAX_RECOMMENDED_AGE'):
        import re as _re
        if _re.match(r'^\d+(\.\d+)?$', val_str):
            return f"{val_str} años"

    return val_str


def build_attributes(ml_attrs: dict, ml_required: list, wc_attrs: dict = None) -> list:
    """
    Construye la lista de atributos para el payload de ML.

    ml_attrs    — dict con atributos del producto: {'brand': 'Samsung', 'color': 'Negro', ...}
    ml_required — lista de atributos de la categoría obtenida de ML API
    wc_attrs    — dict de atributos WC del producto (nombre.lower() → valor)

    Estrategia:
      1. Buscar el valor del atributo en ml_attrs por id o name (case-insensitive)
      2. Si no hay, buscar en wc_attrs via WC_TO_ML_ID
      3. Si el atributo tiene valores permitidos → buscar match por nombre
      4. Si es REQUIRED y no hay match → usar primer valor disponible
      5. Si acepta texto libre → enviar el valor directamente
    """
    result = []
    _wc = wc_attrs or {}

    for attr in ml_required:
        attr_id   = attr.get('id', '')
        attr_name = attr.get('name', '')
        tags      = attr.get('tags', {})
        is_required = tags.get('required', False)
        allowed_vals = attr.get('values', [])

        # Omitir tipos especiales que ML gestiona internamente o que requieren IDs específicos:
        # grid_id/grid_row_id → requieren una guía de tallas real (no se puede inferir)
        # picture             → requiere un picture_id, no texto
        if attr.get('value_type') in _SKIP_VALUE_TYPES:
            continue

        # Buscar valor en ml_attrs por id del atributo o por nombre.
        # Incluye lookup directo por ML ID (ej: ml_attrs tiene 'COLOR': 'Negro'
        # cuando atributos_ia.py genera con IDs de ML como clave).
        value = (
            ml_attrs.get(attr_id) or          # clave exacta ML ID  (ej: 'COLOR')
            ml_attrs.get(attr_id.lower()) or   # clave lowercase     (ej: 'color')
            ml_attrs.get(attr_name.lower()) or
            ml_attrs.get(_normalize(attr_id)) or
            ml_attrs.get(_normalize(attr_name))
        )

        # Si no hay valor en ml_attrs, buscar en wc_attrs:
        # 1) directo por ML ID (cuando WC attribute name ES el ML ID, ej: 'color' → attr COLOR)
        # 2) via WC_TO_ML_ID (nombres en español)
        if not value and _wc:
            value = _wc.get(attr_id.lower())   # WC attribute cuyo nombre es el ML ID
        if not value and _wc:
            for wc_name, ml_id in WC_TO_ML_ID.items():
                ml_ids = ml_id if isinstance(ml_id, list) else [ml_id]
                if attr_id in ml_ids and wc_name in _wc:
                    value = _wc[wc_name]
                    break

        if value:
            if allowed_vals:
                # Buscar match en valores permitidos
                matched_id = _find_value_id(value, allowed_vals)
                if matched_id:
                    result.append({'id': attr_id, 'value_id': matched_id})
                elif is_required:
                    # Required pero sin match → primer valor disponible
                    result.append({'id': attr_id, 'value_id': allowed_vals[0]['id']})
                # else: no requerido y sin match → omitir
            else:
                # Acepta texto libre — validar según value_type
                val_str = str(value).strip()
                validated = _validate_value(attr, val_str)
                if validated is None:
                    continue  # tipo incompatible (ej: "N/A" para number) → omitir
                result.append({'id': attr_id, 'value_name': validated})

        elif is_required and allowed_vals:
            # Required, sin valor disponible → primer valor por defecto
            result.append({'id': attr_id, 'value_id': allowed_vals[0]['id']})

    return result


def _find_value_id(value: str, allowed_vals: list) -> str | None:
    """Busca el ID del valor permitido más cercano al valor dado."""
    value_norm = _normalize(value)
    value_tokens = set(value_norm.split())

    # 1. Match exacto
    for v in allowed_vals:
        if _normalize(v.get('name', '')) == value_norm:
            return v['id']

    # 2. Match parcial (substring)
    for v in allowed_vals:
        v_norm = _normalize(v.get('name', ''))
        if value_norm in v_norm or v_norm in value_norm:
            return v['id']

    # 3. Match por tokens: todos los tokens del valor ML están en el valor WC
    #    Ej: "usb c" (tokens: usb, c) ⊆ tokens de "usb tipo c" (usb, tipo, c) → match
    best_id, best_score = None, 0
    for v in allowed_vals:
        v_norm = _normalize(v.get('name', ''))
        v_tokens = set(v_norm.split())
        if not v_tokens:
            continue
        overlap = len(v_tokens & value_tokens) / len(v_tokens)
        if overlap > best_score:
            best_score = overlap
            best_id = v['id']
    if best_score >= 1.0:   # todos los tokens del valor ML presentes en WC
        return best_id

    return None


def _normalize(text: str) -> str:
    """Normaliza texto para comparación: minúsculas, sin espacios extra, separa número-unidad."""
    import re
    t = text.lower().strip().replace('_', ' ').replace('-', ' ')
    t = re.sub(r'(\d)([a-z])', r'\1 \2', t)   # "120v" -> "120 v", "29.4v" -> "29.4 v"
    t = re.sub(r'([a-z])(\d)', r'\1 \2', t)   # "v120" -> "v 120"
    return t


def separate_required_optional(ml_category_attrs: list) -> tuple[list, list]:
    """
    Separa los atributos de una categoría en requeridos y opcionales.
    Útil para debug.
    """
    required = [a for a in ml_category_attrs if a.get('tags', {}).get('required')]
    optional = [a for a in ml_category_attrs if not a.get('tags', {}).get('required')]
    return required, optional

def build_secondary_attributes(prod: dict, category_attrs: list, existing_ids: set) -> list:
    """
    Intenta llenar los atributos opcionales (caracteristicas secundarias) de la categoria
    usando datos disponibles del producto WC: ml_attrs, wc_attrs y dimensiones.
    existing_ids: IDs de atributos ya agregados (para no duplicar).
    """
    result = []
    all_data: dict = {}
    all_data.update(prod.get('wc_attrs', {}))
    all_data.update(prod.get('ml_attrs', {}))

    for attr in category_attrs:
        attr_id   = attr.get('id', '')
        attr_name = attr.get('name', '')
        tags      = attr.get('tags', {})

        if attr_id in existing_ids:
            continue
        if tags.get('hidden') or tags.get('read_only') or tags.get('used_hidden'):
            continue
        # BRAND y MODEL siempre se fijan en build_payload — nunca sobreescribir
        if attr_id in ('BRAND', 'MODEL'):
            continue

        # Omitir tipos especiales que ML gestiona internamente o requieren IDs:
        # grid_id/grid_row_id → tabla de talles real; picture → picture_id real
        if attr.get('value_type') in _SKIP_VALUE_TYPES:
            continue

        allowed_vals = attr.get('values', [])

        # Para atributos de dimensión, usar _get_dimension_value (unidades correctas)
        dim_value = _get_dimension_value(attr_id, attr_name, prod)

        if tags.get('required'):
            # Buscar valor en wc_attrs via WC_TO_ML_ID
            wc_value = None
            for wc_name, ml_id in WC_TO_ML_ID.items():
                ml_ids = ml_id if isinstance(ml_id, list) else [ml_id]
                if attr_id in ml_ids and wc_name in all_data:
                    wc_value = all_data[wc_name]
                    break
            req_value = dim_value or wc_value
            if req_value:
                if allowed_vals and not dim_value:
                    matched_id = _find_value_id(str(req_value), allowed_vals)
                    if matched_id:
                        result.append({'id': attr_id, 'value_id': matched_id})
                elif not allowed_vals:
                    result.append({'id': attr_id, 'value_name': req_value})
            continue

        # Atributos opcionales: buscar en wc_attrs/ml_attrs primero, luego dimensiones
        # También buscar via WC_TO_ML_ID: si algún nombre WC mapea a este attr_id
        wc_value = None
        for wc_name, ml_id in WC_TO_ML_ID.items():
            ml_ids = ml_id if isinstance(ml_id, list) else [ml_id]
            if attr_id in ml_ids and wc_name in all_data:
                wc_value = all_data[wc_name]
                break

        value = (
            wc_value or
            all_data.get(attr_id) or           # clave exacta ML ID  (ej: 'COLOR')
            all_data.get(attr_id.lower()) or   # clave lowercase      (ej: 'color')
            all_data.get(_normalize(attr_id)) or
            all_data.get(_normalize(attr_name))
        )

        # Para atributos de dimensión, preferir el valor con unidad correcta
        if dim_value:
            value = dim_value

        if not value:
            continue

        if allowed_vals:
            matched_id = _find_value_id(str(value), allowed_vals)
            if matched_id:
                result.append({'id': attr_id, 'value_id': matched_id})
            else:
                # Sin match en allowed_vals → omitir (ML rechaza value_name para attrs con lista)
                pass
        else:
            val_str = str(value).strip()
            validated = _validate_value(attr, val_str)
            if validated is None:
                continue  # tipo incompatible (ej: "N/A" para number) → omitir
            # Para atributos de texto libre sin value_type especial, normalizar fracciones
            if attr.get('value_type') not in ('number', 'number_unit') and attr_id not in ('MIN_RECOMMENDED_AGE', 'MAX_RECOMMENDED_AGE'):
                validated = _resolve_fraction(validated)
            result.append({'id': attr_id, 'value_name': validated})

    return result


def _format_number_unit(value: str, default_unit: str, allowed_units: list = None) -> str | None:
    """
    Para atributos de tipo number_unit:
    - Si el valor es un número puro → agrega default_unit
    - Si el valor tiene número + unidad → valida la unidad contra allowed_units;
      si no es válida, usa default_unit
    - Si el valor NO es un número válido (texto libre) → retorna None (omitir)
    """
    import re
    val = _resolve_fraction(value.strip())  # "3/8 pulgadas" → "0.375 pulgadas"
    m = re.match(r'^(\d+(?:[.,]\d+)?)\s*(.*)$', val)
    if m:
        num_part = m.group(1).replace(',', '.')
        unit_part = m.group(2).strip()
        if unit_part:
            # Validar unidad contra allowed_units
            if allowed_units:
                valid_ids = {u.get('id', '').lower() for u in allowed_units}
                if unit_part.lower() not in valid_ids:
                    unit_part = default_unit  # unidad inválida → usar default
            return f"{num_part} {unit_part}" if unit_part else None  # sin unidad válida → omitir
        elif default_unit:
            return f"{num_part} {default_unit}"
        else:
            return None  # número sin unidad y sin default_unit → omitir (ML rechaza número puro)
    return None  # valor no numérico → omitir


def _resolve_fraction(value: str) -> str:
    """
    Convierte fracciones en strings a decimales para que ML las acepte.
    Ej: '3/8 pulgadas' → '0.375 pulgadas'
        '1/2'          → '0.5'
        '3/4 in'       → '0.75 in'
    """
    import re
    def frac_to_decimal(m):
        num, den = int(m.group(1)), int(m.group(2))
        if den == 0:
            return m.group(0)
        result = num / den
        # Mostrar sin decimales innecesarios
        return str(int(result)) if result == int(result) else f"{result:.4g}"

    return re.sub(r'(\d+)\s*/\s*(\d+)', frac_to_decimal, value)


def _get_dimension_value(attr_id: str, attr_name: str, prod: dict) -> str | None:
    aid   = attr_id.lower()
    aname = attr_name.lower()
    keywords = {
        'length': ['length', 'largo', 'longitud'],
        'width':  ['width',  'ancho', 'anchura'],
        'height': ['height', 'altura', 'alto'],
        'weight': ['weight', 'peso'],
    }
    for dim, kws in keywords.items():
        for kw in kws:
            if kw in aid or kw in aname:
                raw = prod.get(dim)
                if not raw:
                    return None
                try:
                    if dim == 'weight':
                        # WC guarda en kg, ML requiere gramos enteros
                        return f"{int(round(float(raw) * 1000))} g"
                    else:
                        # dimensiones en cm enteros
                        return f"{int(round(float(raw)))} cm"
                except (ValueError, TypeError):
                    return None
    return None
