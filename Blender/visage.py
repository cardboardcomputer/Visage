import bpy
import sys
import gc
import time
import math
import queue
import threading
import multiprocessing
from pythonosc import dispatcher, osc_server


bl_info = {
    'name': 'Visage',
    'author': 'Cardboard Computer',
    'version': (0, 1),
    'blender': (2, 93, 0),
    'description': 'Receives OSC messages from Visage',
    'category': 'Animation',
}


state = None # visage.state is like bpy.context
mp = multiprocessing.get_context('fork')


UPDATE_STEP = 1. / 60.


SHAPE_KEYS = [
    'BrowInnerUp',
    'BrowDownLeft',
    'BrowDownRight',
    'BrowOuterUpLeft',
    'BrowOuterUpRight',
    'EyeLookUpLeft',
    'EyeLookUpRight',
    'EyeLookDownLeft',
    'EyeLookDownRight',
    'EyeLookInLeft',
    'EyeLookInRight',
    'EyeLookOutLeft',
    'EyeLookOutRight',
    'EyeBlinkLeft',
    'EyeBlinkRight',
    'EyeSquintLeft',
    'EyeSquintRight',
    'EyeWideLeft',
    'EyeWideRight',
    'CheekPuff',
    'CheekSquintLeft',
    'CheekSquintRight',
    'NoseSneerLeft',
    'NoseSneerRight',
    'JawOpen',
    'JawForward',
    'JawLeft',
    'JawRight',
    'MouthFunnel',
    'MouthPucker',
    'MouthLeft',
    'MouthRight',
    'MouthRollUpper',
    'MouthRollLower',
    'MouthShrugUpper',
    'MouthShrugLower',
    'MouthClose',
    'MouthSmileLeft',
    'MouthSmileRight',
    'MouthFrownLeft',
    'MouthFrownRight',
    'MouthDimpleLeft',
    'MouthDimpleRight',
    'MouthUpperUpLeft',
    'MouthUpperUpRight',
    'MouthLowerDownLeft',
    'MouthLowerDownRight',
    'MouthPressLeft',
    'MouthPressRight',
    'MouthStretchLeft',
    'MouthStretchRight',
    'TongueOut',
]


SHAPE_KEY_IDX_TO_NAME = {}
for i, n in enumerate(SHAPE_KEYS):
    SHAPE_KEY_IDX_TO_NAME[i] = n


SHAPE_KEY_NAME_TO_IDX = {}
for i, n in enumerate(SHAPE_KEYS):
    SHAPE_KEY_NAME_TO_IDX[n] = i


SHAPE_KEYS_MIRROR_LEFT = {}
for n in SHAPE_KEYS:
    if n.endswith('Left'):
        m = n.replace('Left', 'Right')
        SHAPE_KEYS_MIRROR_LEFT[n] = SHAPE_KEYS[SHAPE_KEYS.index(m)]
    elif n.endswith('Right'):
        pass
    else:
        SHAPE_KEYS_MIRROR_LEFT[n] = n


SHAPE_KEYS_MIRROR_RIGHT = {}
for n in SHAPE_KEYS:
    if n.endswith('Right'):
        m = n.replace('Right', 'Left')
        SHAPE_KEYS_MIRROR_RIGHT[n] = SHAPE_KEYS[SHAPE_KEYS.index(m)]
    elif n.endswith('Left'):
        pass
    else:
        SHAPE_KEYS_MIRROR_RIGHT[n] = n


SHAPE_KEY_SETS = [
    ('brow', 0, 5),
    ('eye', 5, 14),
    ('cheek', 19, 3),
    ('nose', 22, 2),
    ('jaw', 24, 4),
    ('mouth', 28, 23),
    ('tongue', 51, 1),
]


SHAPE_KEY_GROUP = {}
for i, n in enumerate(SHAPE_KEYS):
    for group, start, count in SHAPE_KEY_SETS:
        if i >= start and i < (start + count):
            SHAPE_KEY_GROUP[n] = group.capitalize()


del i, n, m, group, start, count # tidying


def lerp(a, b, v):
    return a * (1 - v) + b * v


def remap(v, b, s):
    # return (v - b) * s
    return v * s + b


def redraw_areas():
    for area in bpy.context.screen.areas:
        area.tag_redraw()


def get_timeline_frame():
    return bpy.context.scene.frame_current + bpy.context.scene.frame_subframe


def get_timeline_seconds():
    return get_timeline_frame() / bpy.context.scene.render.fps


def update_weight_params(self, value):
    t = bpy.context.scene.visage_target

    remap_min = t.shape_min1[:] + t.shape_min2[:]
    remap_max = t.shape_max1[:] + t.shape_max2[:]
    enabled = list(t.sub_brow_enabled) if t.brow_enabled else [False] * 5
    enabled += list(t.sub_eye_enabled) if t.eye_enabled else [False] * 14
    enabled += list(t.sub_cheek_enabled) if t.cheek_enabled else [False] * 3
    enabled += list(t.sub_nose_enabled) if t.nose_enabled else [False] * 2
    enabled += list(t.sub_jaw_enabled) if t.jaw_enabled else [False] * 4
    enabled += list(t.sub_mouth_enabled) if t.mouth_enabled else [False] * 23
    enabled += list(t.sub_tongue_enabled) if t.tongue_enabled else [False]

    for i, data in enumerate(state.weight_params):
        data[0] = remap_min[i]
        data[1] = remap_max[i]
        data[2] = enabled[i]


def update_neutral(self, value):
    target = bpy.context.scene.visage_target
    state.neutral = target.neutral1[:] + target.neutral2[:]


def apply_visage_data(target, prefs, data):
    key_blocks = target.face.shape_keys.key_blocks
    bones = target.armature.pose.bones

    if target.mirror == 'LEFT':
        mirror = SHAPE_KEYS_MIRROR_LEFT
    elif target.mirror == 'RIGHT':
        mirror = SHAPE_KEYS_MIRROR_RIGHT
    else:
        mirror = None

    weights = data[:52]
    if target.apply_neutral:
        weights = [x - y for (x, y) in zip(weights, state.neutral[:52])]

    if mirror:
        for i, weight in enumerate(weights):
            bias, scale, enabled = state.weight_params[i]
            if enabled:
                name = SHAPE_KEY_IDX_TO_NAME[i]
                value = remap(weight, bias, scale)
                other = mirror.get(name)
                if other:
                    key_blocks[name].value = value
                    key_blocks[other].value = value
    else:
        for i, weight in enumerate(weights):
            bias, scale, enabled = state.weight_params[i]
            if enabled:
                key_blocks[SHAPE_KEY_IDX_TO_NAME[i]].value = remap(weight, bias, scale)

    head_pos = data[52:55]
    if target.apply_neutral:
        head_pos = [x - y for (x, y) in zip(head_pos, state.neutral[52:55])]

    if target.head_pos_enabled:
        bones[target.head].location = head_pos

    head_rot = data[55:58]
    if target.apply_neutral:
        head_rot = [x - y for (x, y) in zip(head_rot, state.neutral[55:58])]

    if target.head_rot_enabled:
        bs = target.head_rot_min_max[:]
        bones[target.head].rotation_euler = [
            remap(math.radians(head_rot[0]), *bs),
            remap(math.radians(head_rot[1]), *bs),
            remap(math.radians(head_rot[2]), *bs)]

    eye_l_rot = data[58:60]
    eye_r_rot = data[60:62]

    if target.eyes_rot_enabled:
        bs = target.eyes_rot_min_max[:]
        bones[target.eye_left].rotation_euler = [
            remap(math.radians(v), *bs) for v in eye_l_rot] + [0]
        bones[target.eye_right].rotation_euler = [
            remap(math.radians(v), *bs) for v in eye_r_rot] + [0]

    target.face.update_tag()


def record_visage_data(target, prefs):
    scene = bpy.context.scene
    state.recording[scene.frame_current] = state.input_frame[:]


def keyframe_visage_recording(target, prefs):
    for frame, data in state.recording.items():
        offset_frame = frame - prefs.frame_latency
        apply_visage_data(target, prefs, data)
        weights = data[:52]

        for i, weight in enumerate(weights):
            shape = SHAPE_KEY_IDX_TO_NAME[i]
            bias, scale, enabled = state.weight_params[i]
            if enabled:
                target.face.shape_keys.keyframe_insert(
                    frame=offset_frame,
                    group=SHAPE_KEY_GROUP[shape],
                    data_path='key_blocks["%s"].value' % shape)
        if target.head_pos_enabled:
            target.armature.keyframe_insert(
                frame=offset_frame,
                data_path='pose.bones["%s"].location' % target.head)
        if target.head_rot_enabled:
            target.armature.keyframe_insert(
                frame=offset_frame,
                data_path='pose.bones["%s"].rotation_euler' % target.head)
        if target.eyes_rot_enabled:
            target.armature.keyframe_insert(
                frame=offset_frame,
                data_path='pose.bones["%s"].rotation_euler' % target.eye_left)
            target.armature.keyframe_insert(
                frame=offset_frame,
                data_path='pose.bones["%s"].rotation_euler' % target.eye_right)

    state.recording.clear()
    gc.collect()


def timer_preview_update():
    # wraper function needed for unregister
    return state.preview_update()


def timer_record_update():
    # wraper function needed for unregister
    return state.record_update()


def handler_frame_change_post(scene):
    wm = bpy.context.window_manager
    screen = bpy.context.screen

    if wm.visage_preview:
        apply_visage_data(state.target, state.prefs, state.input_frame)

    if (not state.use_remote_timing
        and wm.visage_record
        and screen.is_animation_playing
        and not screen.is_scrubbing):

        if state.receiver.wait_for_frame():
            record_visage_data(state.target, state.prefs)


def maybe_toggle_frame_change_handler():
    wm = bpy.context.window_manager
    if wm.visage_preview or wm.visage_record:
        if handler_frame_change_post not in bpy.app.handlers.frame_change_post:
            bpy.app.handlers.frame_change_post.append(handler_frame_change_post)
    else:
        if handler_frame_change_post in bpy.app.handlers.frame_change_post:
            bpy.app.handlers.frame_change_post.remove(handler_frame_change_post)


class VisageState:
    # local singleton only

    '''
    `input_status`:
        0:  receiving, thread/fork is running
        1:  recording, queueing up frames for remote timing
        2:  new frame received flag

    `input_timing`:
        0: recording start (seconds on timeline)
        1: recording start (broadcast uptime)

    `input_frame`:
        the latest frame data received

    `input_buffer`:
        queue of received frame data when using remote timing
    '''

    def __init__(self):
        self.receiver = None
        self.neutral = [0.] * 62
        self.recording = {}
        self.use_remote_timing = False

        self.weight_params = []
        for i in range(52):
            self.weight_params.append([0., 1., True]) # [bias, scale, enabled]

        self.fork = True if sys.platform == 'linux' else False

        if self.fork:
            self.input_status = mp.Array('i', [0, 0, 0], lock=False)
            self.input_timing = mp.Array('d', [0, 0], lock=False)
            self.input_frame = mp.Array('d', [0] * 63, lock=False)
            self.input_buffer = mp.Queue()
        else:
            self.input_status = [0, 0, 0]
            self.input_timing = [0, 0]
            self.input_frame = [0] * 63
            self.input_buffer = queue.Queue()

    @property
    def target(self):
        return bpy.context.scene.visage_target

    @property
    def prefs(self):
        return bpy.context.preferences.addons['visage'].preferences

    @property
    def is_receiver_running(self):
        return self.receiver and self.receiver.is_running

    def load_neutral(self):
        scene = bpy.context.scene
        target = scene.visage_target
        # if 'visage_neutral' in scene:
        #     self.neutral = list(scene['visage_neutral'])
        self.neutral = target.neutral1[:] + target.neutral2[:]

    def start_receiver(self):
        self.use_remote_timing = self.target.keyframe_source == 'BROADCAST'
        if self.receiver is not None:
            self.receiver.reset()
        else:
            self.receiver = VisageReceiver(self.prefs.host, self.prefs.port, self.fork)
        self.receiver.start()

    def stop_receiver(self):
        if self.receiver is not None:
            self.receiver.stop()
        self.receiver = None

    def preview_update(self):
        if self.receiver:
            apply_visage_data(self.target, self.prefs, self.input_frame)
        return UPDATE_STEP

    def record_update(self):
        wm = bpy.context.window_manager

        if (wm.visage_record
            and self.use_remote_timing
            and self.receiver is not None):

            screen = bpy.context.screen
            offset = self.input_timing[0]
            fps = bpy.context.scene.render.fps
            is_playing = screen.is_animation_playing and not screen.is_scrubbing

            if is_playing and not self.receiver.is_recording:
                self.receiver.start_recording()
            if not is_playing and self.receiver.is_recording:
                self.receiver.stop_recording()

            while not self.input_buffer.empty():
                data = self.input_buffer.get_nowait()
                if data:
                    frame = (offset + data[-1]) * fps
                    self.recording[frame] = data

        return UPDATE_STEP


class VisageReceiver:
    # local singleton only
    def __init__(self, host, port, fork=False):
        self.host = host
        self.port = port
        self.fork = fork
        self.process = None
        self.server = None
        self.sleep = 0

    @property
    def is_running(self):
        return state.input_status[0] == 1

    @property
    def is_recording(self):
        return state.input_status[1] == 1

    def start(self):
        if self.process and self.process.is_alive():
            return
        else:
            state.input_status[0] = 1
            if self.fork:
                method = mp.Process
            else:
                method = threading.Thread
                self.sleep = UPDATE_STEP
            self.process = method(
                target=self.loop, args=(
                    state.input_status,
                    state.input_timing,
                    state.input_frame,
                    state.input_buffer))
            self.process.start()

    def stop(self):
        state.input_status[0] = 2

    def start_recording(self):
        state.input_status[1] = 1
        state.input_timing[0] = get_timeline_seconds()

    def stop_recording(self):
        state.input_status[1] = 0

    def timeout(self):
        if self.server:
            self.server.timed_out = True

    def wait_for_frame(self, iterations=60):
        count = 0
        while state.input_status[2] == 0:
            time.sleep(UPDATE_STEP)
            count += 1
            if count >= iterations:
                # new frame unavailable yet
                return False
        # new frame available
        state.input_status[2] = 0
        return True

    def loop(self, state, timing, data, frames):
        print('Visage OSC receiver started')

        self.state = state
        self.timing = timing
        self.data = data
        self.frames = frames
        self.marked = False
        self.offset_timeline = 0
        self.offset_timestamp = 0

        dispatch = dispatcher.Dispatcher()
        dispatch.map('/visage', self.receive)

        self.server = server = osc_server.BlockingOSCUDPServer(
            (self.host, self.port), dispatch)
        server.handle_timeout = self.timeout
        server.timeout = 0

        while not state[0] == 2:
            server.timed_out = False
            while not server.timed_out:
                server.handle_request()
            if self.sleep > 0:
                time.sleep(self.sleep)

        print('Visage OSC receiver stopped')

        self.server.server_close()
        self.server = None
        state[0] = 0

    def receive(self, *args):
        data = args[1:]
        is_recording = self.state[1] == 1

        if is_recording and not self.marked:
            self.marked = True
            self.offset_timeline = self.timing[0]
            self.offset_timestamp = self.timing[1] = data[-1]
        if not is_recording and self.marked:
            self.marked = False

        if is_recording:
            timestamp = data[-1] - self.offset_timestamp
            data = data[:-1] + (timestamp,)
            self.frames.put_nowait(data)

        self.data[:] = data
        self.state[2] = 1


class VisagePreferences(bpy.types.AddonPreferences):
    bl_idname = 'visage'

    host : bpy.props.StringProperty(default='localhost', name='Host')
    port : bpy.props.IntProperty(default=8080, name='Port')
    frame_latency : bpy.props.IntProperty(default=0, name='Frame Latency')

    def draw(self, context):
        row = self.layout.row(align=True)
        row.prop(self, 'host', text='Host')
        row.prop(self, 'port', text='Port')
        self.layout.prop(self, 'frame_latency', text='Frame Latency')


class VisageTarget(bpy.types.PropertyGroup):
    face : bpy.props.PointerProperty(type=bpy.types.Mesh, name='Face')
    armature : bpy.props.PointerProperty(type=bpy.types.Object, name='Armature')
    head : bpy.props.StringProperty(default='Head', name='Head')
    eye_left : bpy.props.StringProperty(default='Eye.L', name='Eye.L')
    eye_right : bpy.props.StringProperty(default='Eye.R', name='Eye.R')

    head_rot_enabled : bpy.props.BoolProperty(default=False)
    head_pos_enabled : bpy.props.BoolProperty(default=False)
    eyes_rot_enabled : bpy.props.BoolProperty(default=True)
    head_rot_min_max : bpy.props.FloatVectorProperty(size=2, default=[0, 1])
    eyes_rot_min_max : bpy.props.FloatVectorProperty(size=2, default=[0, 1])

    brow_enabled : bpy.props.BoolProperty(default=True, update=update_weight_params)
    eye_enabled : bpy.props.BoolProperty(default=True, update=update_weight_params)
    cheek_enabled : bpy.props.BoolProperty(default=True, update=update_weight_params)
    nose_enabled : bpy.props.BoolProperty(default=True, update=update_weight_params)
    jaw_enabled : bpy.props.BoolProperty(default=True, update=update_weight_params)
    mouth_enabled : bpy.props.BoolProperty(default=True, update=update_weight_params)
    tongue_enabled : bpy.props.BoolProperty(default=True, update=update_weight_params)

    sub_brow_enabled : bpy.props.BoolVectorProperty(size=5, default=[True]*5, update=update_weight_params)
    sub_eye_enabled : bpy.props.BoolVectorProperty(size=14, default=[True]*14, update=update_weight_params)
    sub_cheek_enabled : bpy.props.BoolVectorProperty(size=3, default=[True]*3, update=update_weight_params)
    sub_nose_enabled : bpy.props.BoolVectorProperty(size=2, default=[True]*2, update=update_weight_params)
    sub_jaw_enabled : bpy.props.BoolVectorProperty(size=4, default=[True]*4, update=update_weight_params)
    sub_mouth_enabled : bpy.props.BoolVectorProperty(size=23, default=[True]*23, update=update_weight_params)
    sub_tongue_enabled : bpy.props.BoolVectorProperty(size=1, default=[True]*1, update=update_weight_params)

    shape_min1 : bpy.props.FloatVectorProperty(size=32, default=[0.0]*32, update=update_weight_params)
    shape_max1 : bpy.props.FloatVectorProperty(size=32, default=[1.0]*32, update=update_weight_params)
    shape_min2 : bpy.props.FloatVectorProperty(size=20, default=[0.0]*20, update=update_weight_params)
    shape_max2 : bpy.props.FloatVectorProperty(size=20, default=[1.0]*20, update=update_weight_params)

    MIRROR_ITEMS = [
        ('NONE', 'No Mirroring', 'No mirroring'),
        ('LEFT', 'Mirror Left', 'Mirror left'),
        ('RIGHT', 'Mirror Right', 'Mirror right'),
    ]

    mirror : bpy.props.EnumProperty(items=MIRROR_ITEMS, default='NONE', name='Mirroring')

    FALLOFF_ITEMS = (
        ('UNIFORM', 'Uniform', 'Uniform'),
        ('LINEAR', 'Linear', 'Linear'),
        ('SQUARE', 'Square', 'Square'),
        ('SQUARE_INVERSE', 'Inverse Square', 'Inverse Square'),
        ('SMOOTH', 'Smooth', 'Smooth'),
        ('SMOOTH_X2', 'Smooth x2', 'Smooth x2'),
    )

    filter_selected_only : bpy.props.BoolProperty()
    filter_samples : bpy.props.IntProperty(default=3)
    filter_falloff : bpy.props.EnumProperty(items=FALLOFF_ITEMS, default='SQUARE_INVERSE')
    filter_bias : bpy.props.FloatProperty(default=0)
    filter_scale : bpy.props.FloatProperty(default=1)

    KEYFRAME_SOURCE_ITEMS = [
        ('TIMELINE', 'Key On Timeline Frame', 'Key on current frame on the timeline'),
        ('BROADCAST', 'Key On Broadcast Frame', 'Key on original broadcast timestamp'),
    ]

    def update_keyframe_source(self, context):
        state.use_remote_timing = self.keyframe_source == 'BROADCAST'

    keyframe_source : bpy.props.EnumProperty(
        items=KEYFRAME_SOURCE_ITEMS,
        default='TIMELINE', name='Keyframe Mode',
        update=update_keyframe_source)

    apply_neutral : bpy.props.BoolProperty(default=False)
    have_neutral : bpy.props.BoolProperty(default=False)
    neutral1 : bpy.props.FloatVectorProperty(size=32, default=[0.0]*32, step=1, update=update_neutral)
    neutral2 : bpy.props.FloatVectorProperty(size=30, default=[0.0]*30, step=1, update=update_neutral)

    # ui display only

    show_brow : bpy.props.BoolProperty()
    show_eye : bpy.props.BoolProperty()
    show_cheek : bpy.props.BoolProperty()
    show_nose : bpy.props.BoolProperty()
    show_jaw : bpy.props.BoolProperty()
    show_mouth : bpy.props.BoolProperty()
    show_tongue : bpy.props.BoolProperty()
    show_neutral : bpy.props.BoolProperty()


class VisagePanelAnimation(bpy.types.Panel):
    bl_idname = 'VS_PT_visage_anim'
    bl_label = 'Animation'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Visage'

    def draw(self, context):
        wm = context.window_manager
        prefs = context.preferences.addons['visage'].preferences
        target = context.scene.visage_target
        layout = self.layout
        col = layout.column(align=True)
        row = col.row(align=True)
        row.scale_y = 2
        row.operator('vs.record', text='RECORD', depress=True if wm.visage_record else False)
        row = col.row(align=True)
        row.scale_y = 1.25
        row.operator('vs.record_save', text='Save (%s)' % len(state.recording))
        row.operator('vs.record_key', text='', icon='KEYFRAME')
        row.operator('vs.record_clear', text='Clear')
        self.layout.prop(prefs, 'frame_latency', text='Frame Latency')
        self.layout.prop(target, 'keyframe_source', text='')


class VisagePanelData(bpy.types.Panel):
    bl_idname = 'VS_PT_visage_data'
    bl_label = 'Data'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Visage'

    def draw(self, context):
        wm = context.window_manager
        prefs = context.preferences.addons['visage'].preferences
        settings = context.scene.visage_target

        row = self.layout.row(align=True)
        row.scale_y = 1.5
        if wm.visage_preview:
            row.operator('vs.preview', text='Preview', depress=True)
        else:
            row.operator('vs.preview', text='Preview', depress=False)
        row.operator('vs.reset', text='Reset')

        col = self.layout.column(align=True)
        row = col.row(align=True)
        row.scale_y = 1.25
        if not state.is_receiver_running:
            row.operator('vs.start', text='Receive', depress=False)
        else:
            row.operator('vs.stop', text='Receive', depress=True)
        col.prop(prefs, 'port', text='Port')
        col.prop(prefs, 'host', text='')


class VisagePanelActor(bpy.types.Panel):
    bl_idname = 'VS_PT_visage_actor'
    bl_label = 'Actor'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Visage'

    def draw(self, context):
        wm = context.window_manager
        prefs = context.preferences.addons['visage'].preferences
        settings = context.scene.visage_target

        row = self.layout.row(align=True)
        row.scale_y = 1.5
        icon = 'KEYFRAME_HLT' if settings.apply_neutral else 'KEYFRAME'
        row.prop(settings, 'apply_neutral', text='', toggle=True, icon=icon)
        # icon = 'CHECKBOX_HLT' if 'visage_neutral' in context.scene else 'CHECKBOX_DEHLT'
        icon = 'CHECKBOX_HLT' if settings.have_neutral else 'CHECKBOX_DEHLT'
        op = row.operator('vs.pose', text='Set Neutral', icon=icon)
        op.reset = False
        op = row.operator('vs.pose', text='', icon='X')
        op.reset = True

        box = self.layout.box()
        row = box.row()
        visible = settings.show_neutral
        if visible:
            row.prop(settings, 'show_neutral', icon='DOWNARROW_HLT', text='', emboss=False)
        else:
            row.prop(settings, 'show_neutral', icon='RIGHTARROW', text='', emboss=False)
        row.label(text='Neutral Offsets')
        if visible:
            col = box.column(align=True)
            for i in range(32):
                col.prop(settings, 'neutral1', index=i, text=SHAPE_KEY_IDX_TO_NAME[i])
            for i in range(20):
                col.prop(settings, 'neutral2', index=i, text=SHAPE_KEY_IDX_TO_NAME[32 + i])
            col.prop(settings, 'neutral2', index=52 - 32, text='Head Pos X')
            col.prop(settings, 'neutral2', index=53 - 32, text='Head Pos Y')
            col.prop(settings, 'neutral2', index=54 - 32, text='Head Pos Z')
            col.prop(settings, 'neutral2', index=55 - 32, text='Head Rot X')
            col.prop(settings, 'neutral2', index=56 - 32, text='Head Rot Y')
            col.prop(settings, 'neutral2', index=57 - 32, text='Head Rot Z')
            col.prop(settings, 'neutral2', index=58 - 32, text='Eye.L X')
            col.prop(settings, 'neutral2', index=59 - 32, text='Eye.L Y')
            col.prop(settings, 'neutral2', index=60 - 32, text='Eye.R X')
            col.prop(settings, 'neutral2', index=61 - 32, text='Eye.R Y')

        col = self.layout.column(align=True)
        row = col.row(align=True)
        row.scale_y = 1.25
        op = row.operator('vs.neutral', text='Apply')
        op.remove = False
        op = row.operator('vs.neutral', text='Remove')
        op.remove = True
        box = col.box()
        box.scale_y = .8
        action_face = action_body =  None
        tokens = []
        if settings.face and settings.face.shape_keys:
            anim_data = settings.face.shape_keys.animation_data
            if anim_data:
                action_face = anim_data.action
                if action_face:
                    box.label(text='Face: %s' % action_face.name)
        if settings.armature:
            anim_data = settings.armature.animation_data
            if anim_data:
                action_body = anim_data.action
                if action_body:
                    box.label(text='Body: %s' % action_body.name)



class VisagePanelTarget(bpy.types.Panel):
    bl_idname = 'VS_PT_visage_target'
    bl_label = 'Target'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Visage'

    def draw(self, context):
        wm = context.window_manager
        prefs = context.preferences.addons['visage'].preferences
        settings = context.scene.visage_target

        col = self.layout.column(align=True)
        col.prop(settings, 'face')
        col.prop(settings, 'armature')
        col.prop(settings, 'head')
        col.prop(settings, 'eye_left')
        col.prop(settings, 'eye_right')

        self.layout.operator('vs.visage_shape_keys', text='Create Shape Keys')


class VisagePanelKeys(bpy.types.Panel):
    bl_idname = 'VS_PT_visage_keys'
    bl_label = 'Parameters'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Visage'

    def draw(self, context):
        wm = context.window_manager
        prefs = context.preferences.addons['visage'].preferences
        settings = context.scene.visage_target
        layout = self.layout

        col = layout.column()
        col.prop(settings, 'mirror', text='')
        col.prop(settings, 'head_pos_enabled', text='Head Position')
        col.prop(settings, 'head_rot_enabled', text='Head Rotation')
        r = col.row(align=True)
        r.enabled = settings.head_rot_enabled
        r.prop(settings, 'head_rot_min_max', index=0, text='')
        r.prop(settings, 'head_rot_min_max', index=1, text='')
        col.prop(settings, 'eyes_rot_enabled', text='Eye Rotation')
        r = col.row(align=True)
        r.enabled = settings.eyes_rot_enabled
        r.prop(settings, 'eyes_rot_min_max', index=0, text='')
        r.prop(settings, 'eyes_rot_min_max', index=1, text='')

        col = layout.column(align=True)

        for label, start, length in SHAPE_KEY_SETS:
            box = col.box()
            row = box.row()

            attr_show = 'show_%s' % label
            attr_enabled = '%s_enabled' % label
            attr_sub_enabled = 'sub_%s_enabled' % label

            visible = getattr(settings, attr_show)
            if visible:
                row.prop(settings, attr_show, icon='DOWNARROW_HLT', text='', emboss=False)
            else:
                row.prop(settings, attr_show, icon='RIGHTARROW', text='', emboss=False)
            row.prop(settings, attr_enabled, text='')
            row.label(text=label.capitalize())

            if visible:
                col_sub = box.column(align=True)
                if not getattr(settings, attr_enabled):
                    col_sub.enabled = False
                for i in range(length):
                    index = start + i

                    if index < 32:
                        attr_min = 'shape_min1'
                        attr_max = 'shape_max1'
                        offset = 0
                    else:
                        attr_min = 'shape_min2'
                        attr_max = 'shape_max2'
                        offset = 32

                    row = col_sub.row(align=True)
                    split = row.split(factor=0.5, align=True)
                    row_a, row_b = split.row(align=True), split.row(align=True)
                    min_max_index = index - offset
                    row_a.label(text=SHAPE_KEYS[index][len(label):])
                    row_a.prop(settings, attr_sub_enabled, index=i, text='')
                    row_b.prop(settings, attr_min, index=min_max_index, text='')
                    row_b.prop(settings, attr_max, index=min_max_index, text='')


class VisagePanelFilter(bpy.types.Panel):
    bl_idname = 'VS_PT_visage_filter'
    bl_label = 'Filter'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Visage'

    def draw(self, context):
        wm = context.window_manager
        prefs = context.preferences.addons['visage'].preferences
        settings = context.scene.visage_target
        layout = self.layout

        layout.prop(settings, 'filter_selected_only', text='Selected Curves Only')

        col = layout.column(align=True)
        col.scale_y = 1.5
        col.operator('vs.destutter', text='Destutter')

        col = layout.column(align=True)
        op = col.column(align=True)
        op.scale_y = 1.5
        op.operator('vs.smooth', text='Smooth')
        col.prop(settings, 'filter_falloff', text='')
        col.prop(settings, 'filter_samples', text='Samples')
        col.prop(settings, 'filter_bias', text='Bias')
        col.prop(settings, 'filter_scale', text='Scale')


class VisageStart(bpy.types.Operator):
    bl_idname = 'vs.start'
    bl_label = 'Start Visage Receiver'

    def execute(self, context):
        state.start_receiver()
        state.load_neutral()
        return {'FINISHED'}


class VisageStop(bpy.types.Operator):
    bl_idname = 'vs.stop'
    bl_label = 'Stop Visage Receiver'

    def execute(self, context):
        state.stop_receiver()
        return {'FINISHED'}


class VisagePose(bpy.types.Operator):
    bl_idname = 'vs.pose'
    bl_label = 'Set Neutral Face'

    reset : bpy.props.BoolProperty(default=False)

    def execute(self, context):
        target = context.scene.visage_target

        if self.reset:
            target.neutral1 = [0.] * 32
            target.neutral2 = [0.] * 30
            state.neutral = [0.] * 62
            target.have_neutral = False
            # del context.scene['visage_neutral']
        else:
            neutral = state.input_frame[:62]
            target.neutral1 = neutral[:32]
            target.neutral2 = neutral[32:62]
            state.neutral = neutral
            target.have_neutral = True
            # context.scene['visage_neutral'] = state.neutral
        return {'FINISHED'}


class VisageReset(bpy.types.Operator):
    bl_idname = 'vs.reset'
    bl_label = 'Reset Visage Values'

    def execute(self, context):
        wm = context.window_manager
        if wm.visage_preview:
            bpy.ops.vs.preview()
        target = context.scene.visage_target
        for shape in SHAPE_KEYS:
            target.face.shape_keys.key_blocks[shape].value = 0
        target.armature.pose.bones[target.head].rotation_euler = (0, 0, 0)
        target.armature.pose.bones[target.eye_left].rotation_euler = (0, 0, 0)
        target.armature.pose.bones[target.eye_right].rotation_euler = (0, 0, 0)
        return {'FINISHED'}


class VisagePreview(bpy.types.Operator):
    bl_idname = 'vs.preview'
    bl_label = 'Preview'

    def execute(self, context):
        wm = context.window_manager
        wm.visage_preview = not wm.visage_preview
        if wm.visage_preview:
            if not bpy.app.timers.is_registered(timer_preview_update):
                bpy.app.timers.register(timer_preview_update)
        else:
            if bpy.app.timers.is_registered(timer_preview_update):
                bpy.app.timers.unregister(timer_preview_update)
        maybe_toggle_frame_change_handler()
        state.load_neutral()
        return {'FINISHED'}


class VisageRecord(bpy.types.Operator):
    bl_idname = 'vs.record'
    bl_label = 'Record'

    def execute(self, context):
        wm = context.window_manager
        if not state.receiver.is_running:
            bpy.ops.vs.start()
        wm.visage_record = not wm.visage_record
        # if wm.visage_record and not wm.visage_preview:
        #     bpy.ops.vs.preview()
        # elif not wm.visage_record:
        #     wm.visage_preview = False
        if wm.visage_record:
            if not bpy.app.timers.is_registered(timer_record_update):
                bpy.app.timers.register(timer_record_update)
        else:
            if bpy.app.timers.is_registered(timer_record_update):
                bpy.app.timers.unregister(timer_record_update)
        maybe_toggle_frame_change_handler()
        return {'FINISHED'}


class VisageRecordKey(bpy.types.Operator):
    bl_idname = 'vs.record_key'
    bl_label = 'Record Single Keyframe'

    def execute(self, context):
        target = context.scene.visage_target
        prefs = prefs = bpy.context.preferences.addons['visage'].preferences
        record_visage_data(target, prefs)
        return {'FINISHED'}


class VisageRecordSave(bpy.types.Operator):
    bl_idname = 'vs.record_save'
    bl_label = 'Save'

    def execute(self, context):
        wm = context.window_manager
        target = context.scene.visage_target
        prefs = prefs = bpy.context.preferences.addons['visage'].preferences
        if wm.visage_record:
            bpy.ops.vs.record()
        keyframe_visage_recording(target, prefs)
        return {'FINISHED'}


class VisageRecordClear(bpy.types.Operator):
    bl_idname = 'vs.record_clear'
    bl_label = 'Clear'

    def execute(self, context):
        state.recording.clear()
        gc.collect()
        return {'FINISHED'}


class VisageShapeKeys(bpy.types.Operator):
    bl_idname = 'vs.visage_shape_keys'
    bl_label = 'Create Visage Shape Keys'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == 'MESH'

    def execute(self, context):
        obj = context.object
        if not obj.data.shape_keys or 'Basis' not in obj.data.shape_keys.key_blocks:
            basis = obj.shape_key_add(name='Basis', from_mix=False)
        else:
            basis = obj.data.shape_keys.key_blocks['Basis']
        for name in SHAPE_KEYS:
            if name not in obj.data.shape_keys.key_blocks:
                key = obj.shape_key_add(name=name, from_mix=False)
                key.relative_key = basis
        return {'FINISHED'}


class VisageDestutter(bpy.types.Operator):
    bl_idname = 'vs.destutter'
    bl_label = 'Destutter Visage Curves'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.visage_target.face is not None

    def execute(self, context):
        target = context.scene.visage_target
        action = target.face.shape_keys.animation_data.action

        lookup = {}
        for curve in action.fcurves:
            for key in curve.keyframe_points:
                frame = key.co_ui[0]
                value = key.co_ui[1]
                data = lookup.get(frame, {})
                data[curve.data_path] = value
                lookup[frame] = data

        frames = list(lookup.keys())
        dupes = []

        for i, frame in enumerate(frames[1:]):
            i += 1
            this = lookup[frame]
            prev = lookup[frames[i - 1]]
            dupe = True
            for dp, this_val in this.items():
                prev_val = prev.get(dp)
                if prev_val != this_val:
                    dupe = False
                    break
            if dupe:
                dupes.append(frame)

        for dupe in dupes:
            del lookup[dupe]

        for curve in action.fcurves:
            while len(curve.keyframe_points) > 0:
                curve.keyframe_points.remove(curve.keyframe_points[0])
            for frame, data in lookup.items():
                value = data[curve.data_path]
                k = curve.keyframe_points.insert(frame, value, options={'FAST'})
            curve.update()

        redraw_areas()

        return {'FINISHED'}


class VisageSmooth(bpy.types.Operator):
    bl_idname = 'vs.smooth'
    bl_label = 'Smooth Visage Curves'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.visage_target.face is not None

    def execute(self, context):
        target = context.scene.visage_target
        action = target.face.shape_keys.animation_data.action

        splines = {}
        for curve in action.fcurves:
            if target.filter_selected_only:
                if not curve.select:
                    continue
            points = []
            for k in curve.keyframe_points:
                x, y = k.co_ui
                points.append((x, y, k))
            splines[curve.data_path] = points

        samples = target.filter_samples
        samples_total = samples * 2 + 1
        falloff = target.filter_falloff
        bias = target.filter_bias
        scale = target.filter_scale

        if falloff == 'UNIFORM':
            fn = lambda x: 1
        if falloff == 'LINEAR':
            fn = lambda x: x
        if falloff == 'SQUARE':
            fn = lambda x: x**2
        if falloff == 'SQUARE_INVERSE':
            fn = lambda x: 1 - (1 - x)**2
        if falloff == 'SMOOTH':
            fn = lambda x: math.cos(x * math.pi + math.pi) / 2 + 0.5
        if falloff == 'SMOOTH_X2':
            fn = lambda x: math.cos(max(0, min(x * 2, 1)) * math.pi + math.pi) / 2 + 0.5

        for dp, points in splines.items():
            last = len(points) - 1
            for i, p in enumerate(points):
                x, y, k = p
                for n in range(1, samples + 1):
                    a = min(i + n, last)
                    b = max(i - n, 0)
                    right = points[a]
                    left = points[b]
                    x += right[0] + left[0]
                    y += right[1] + left[1]
                x /= samples_total
                y /= samples_total
                v = max(0, min(remap(p[1], bias, scale), 1))
                f = fn(v)
                x = lerp(p[0], x, f)
                y = lerp(p[1], y, f)
                k.co_ui[0] = x
                k.co_ui[1] = y

        for curve in action.fcurves:
            if len(curve.keyframe_points):
                key = curve.keyframe_points[0]
                key.co_ui[0] = round(key.co_ui[0])
                key = curve.keyframe_points[-1]
                key.co_ui[0] = round(key.co_ui[0])
            curve.update()

        redraw_areas()

        return {'FINISHED'}


class VisageNeutral(bpy.types.Operator):
    bl_idname = 'vs.neutral'
    bl_label = 'Apply Neutral Pose'
    bl_options = {'REGISTER', 'UNDO'}

    remove : bpy.props.BoolProperty(default=False)

    @classmethod
    def poll(cls, context):
        return context.scene.visage_target.face is not None

    def execute(self, context):
        target = context.scene.visage_target
        action = target.face.shape_keys.animation_data.action

        state.load_neutral()
        mode = 1 if self.remove else -1

        if action:
            for curve in action.fcurves:
                if target.filter_selected_only:
                    if not curve.select:
                        continue
                tokens = curve.data_path.split('"')
                if len(tokens) > 1:
                    index = SHAPE_KEY_NAME_TO_IDX.get(tokens[1])
                    if index:
                        offset = state.neutral[index]
                        for k in curve.keyframe_points:
                            k.co_ui.y += offset * mode

        if target.armature:
            action = target.armature.animation_data.action
            if action:
                dp_head_pos = 'pose.bones["%s"].location' % target.head
                dp_head_rot = 'pose.bones["%s"].rotation_euler' % target.head
                dp_eye_l_rot = 'pose.bones["%s"].rotation_euler' % target.eye_left
                dp_eye_r_rot = 'pose.bones["%s"].rotation_euler' % target.eye_right

                for curve in action.fcurves:
                    offset = 0

                    if curve.data_path == dp_head_pos:
                        offset = state.neutral[52 + curve.array_index]
                    if curve.data_path == dp_head_rot:
                        offset = state.neutral[55 + curve.array_index]
                    if curve.data_path == dp_eye_l_rot and curve.array_index < 2:
                        offset = state.neutral[58 + curve.array_index]
                    if curve.data_path == dp_eye_r_rot and curve.array_index < 2:
                        offset = state.neutral[60 + curve.array_index]

                    for k in curve.keyframe_points:
                        k.co_ui.y += offset * mode

        return {'FINISHED'}


__REGISTER_CLASSES__ = (
    VisagePreferences,
    VisageTarget,
    VisagePanelAnimation,
    VisagePanelData,
    VisagePanelActor,
    VisagePanelTarget,
    VisagePanelKeys,
    VisagePanelFilter,
    VisageStart,
    VisageStop,
    VisagePose,
    VisageReset,
    VisagePreview,
    VisageRecord,
    VisageRecordKey,
    VisageRecordSave,
    VisageRecordClear,
    VisageShapeKeys,
    VisageDestutter,
    VisageSmooth,
    VisageNeutral,
)


__REGISTER_PROPS__ = (
    (bpy.types.Scene, 'visage_target', bpy.props.PointerProperty(type=VisageTarget)),
    (bpy.types.WindowManager, 'visage_preview', bpy.props.BoolProperty()),
    (bpy.types.WindowManager, 'visage_record', bpy.props.BoolProperty()),
)


@bpy.app.handlers.persistent
def handler_load_pre(*args):
    if state is not None:
        state.stop_receiver()

    if handler_frame_change_post in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(handler_frame_change_post)

    if bpy.app.timers.is_registered(timer_preview_update):
        bpy.app.timers.unregister(timer_preview_update)

    if bpy.app.timers.is_registered(timer_record_update):
        bpy.app.timers.unregister(timer_record_update)


def register():
    global state

    if state is None:
        state = VisageState()

    for cls in __REGISTER_CLASSES__:
        bpy.utils.register_class(cls)

    for obj, prop, value in __REGISTER_PROPS__:
        setattr(obj, prop, value)

    bpy.app.handlers.load_pre.append(handler_load_pre)


def unregister():
    if state is not None:
        state.stop_receiver()

    for cls in __REGISTER_CLASSES__:
        bpy.utils.unregister_class(cls)

    for cls, prop, value in __REGISTER_PROPS__:
        delattr(cls, prop)

    bpy.app.handlers.load_pre.remove(handler_load_pre)
