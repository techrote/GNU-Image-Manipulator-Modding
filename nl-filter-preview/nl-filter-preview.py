#!/usr/bin/env python3
"""GIMP 3 render tools. GPL-3.0-or-later. Uses the installed NL Filter engine."""
import os, sys, traceback, json, math
from contextlib import contextmanager
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'vendor'))
import gi
gi.require_version('Gimp', '3.0')
gi.require_version('GimpUi', '3.0')
gi.require_version('Gegl', '0.4')
gi.require_version('Gtk', '3.0')
from gi.repository import Gimp, GimpUi, Gegl, Gtk, GdkPixbuf, GLib, GObject

FLAGS = GObject.ParamFlags.READWRITE

NATIVE_FILTERS = ('alpha-trim','optimal-estimation','edge-enhancement')
SCALE_OPTIONS = ('lobes','mitchell-b','mitchell-c','kaiser-beta','gaussian-sigma',
                 'antialias','linear-light','sharpen','sharpen-radius')

def layer_pixels(layer):
    w,h = layer.get_width(),layer.get_height()
    if w*h>40_000_000: raise ValueError('Layer exceeds the 40-megapixel limit.')
    return bytes(layer.get_buffer().get(Gegl.Rectangle.new(0,0,w,h),1.,"R'G'B'A float",Gegl.AbyssPolicy.NONE))

def write_pixels(layer,pixels):
    w,h = layer.get_width(),layer.get_height()
    buffer = layer.get_shadow_buffer()
    buffer.set(Gegl.Rectangle.new(0,0,w,h),"R'G'B'A float",pixels)
    buffer.flush()
    layer.merge_shadow(True)
    layer.update(0,0,w,h)

def native_nl(image,layer,alpha,radius,mode):
    proc = Gimp.get_pdb().lookup_procedure('plug-in-nl-filter')
    cfg = proc.create_config()
    cfg.set_property('run-mode', Gimp.RunMode.NONINTERACTIVE)
    cfg.set_property('image', image)
    cfg.set_core_object_array('drawables', [layer])
    for key,value in (('alpha',alpha),('radius',radius),('filter',mode)):
        cfg.set_property(key,value)
    result = proc.run(cfg)
    if result.index(0) != Gimp.PDBStatusType.SUCCESS:
        raise RuntimeError('Native NL Filter failed: ' + str(result.index(1)))

def nl_apply(image,layer,config):
    alpha,radius,mode = [config.get_property(k) for k in ('alpha','radius','filter')]
    if mode in NATIVE_FILTERS and alpha<=1 and radius<=1:
        native_nl(image,layer,alpha,radius,mode)
        return
    w,h = layer.get_width(),layer.get_height()
    source = layer_pixels(layer)
    if mode not in NATIVE_FILTERS:
        result = worker_job({'operation':'filter','width':w,'height':h,'algorithm':mode,
                             'alpha':alpha,'radius':radius,
                             'edge-threshold':config.get_property('edge-threshold')},source,w*h*16)
    else:
        duplicate = image.duplicate()
        try:
            target = duplicate.get_layers()[image.get_layers().index(layer)]
            Gimp.Selection.none(duplicate)
            passes = max(1,int(math.floor(radius)))
            for _ in range(passes): native_nl(duplicate,target,min(alpha,1.),min(radius,1.),mode)
            result = layer_pixels(target)
            fraction = radius-passes if radius>1 else 0
            if fraction>1e-7:
                before = result
                native_nl(duplicate,target,min(alpha,1.),1.,mode)
                result = worker_job({'operation':'blend','width':w,'height':h,'gain':fraction},
                                    before+layer_pixels(target),w*h*16)
            if alpha>1:
                result = worker_job({'operation':'blend','width':w,'height':h,'gain':alpha},source+result,w*h*16)
        finally:
            duplicate.delete()
    write_pixels(layer,result)

class Preview:
    def __init__(self, image, layer, config, parent):
        self.image, self.layer, self.config = image, layer, config
        self.pixbuf = None
        self.pending = 0
        self.rendering = False
        self.closed = False
        self.generation = 0
        self.window = Gtk.Window(title='NL Filter — Render Preview')
        self.window.set_transient_for(parent)
        self.window.set_default_size(900, 650)
        self.window.connect('delete-event', self.hide)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.window.add(box)
        bar = Gtk.Box(spacing=6)
        box.pack_start(bar, False, False, 0)
        self.zoom = Gtk.SpinButton.new_with_range(5, 800, 5)
        self.zoom.set_value(100)
        self.zoom.connect('value-changed', lambda *_: self.redraw())
        for label, callback in [('Render Preview', self.render), ('Fit', self.fit),
                                ('100%', lambda *_: self.zoom.set_value(100))]:
            button = Gtk.Button(label=label)
            button.connect('clicked', callback)
            bar.pack_start(button, False, False, 0)
        bar.pack_start(Gtk.Label(label='Zoom %'), False, False, 0)
        bar.pack_start(self.zoom, False, False, 0)
        self.before = Gtk.CheckButton(label='Show original')
        self.before.connect('toggled', lambda *_: self.redraw())
        bar.pack_start(self.before, False, False, 0)
        self.status = Gtk.Label(label='Automatic preview. Scroll to pan; zoom is retained when settings change.')
        self.notify_id = config.connect('notify', self.settings_changed)
        self.window.connect('show',lambda *_: self.schedule())
        box.pack_start(self.status, False, False, 0)
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_overlay_scrolling(False)
        box.pack_start(self.scroll, True, True, 0)
        self.canvas = Gtk.DrawingArea()
        self.canvas.connect('draw', self.draw)
        self.scroll.add(self.canvas)

    def settings_changed(self, *_):
        if not self.closed:
            self.status.set_text('Settings changed — updating preview…')
            self.schedule()

    def schedule(self):
        if self.pending:
            GLib.source_remove(self.pending)
            self.pending = 0
        if not self.closed and self.window.get_visible():
            self.pending = GLib.timeout_add(300,self.scheduled_render)

    def scheduled_render(self):
        self.pending = 0
        if not self.closed and self.window.get_visible(): self.render()
        return False

    def close(self):
        self.closed = True
        if self.pending:
            GLib.source_remove(self.pending)
            self.pending = 0
        self.config.disconnect(self.notify_id)
        self.window.destroy()

    def hide(self, *_):
        self.window.hide()
        if self.pending:
            GLib.source_remove(self.pending)
            self.pending = 0
        return True

    def render(self, *_):
        if self.closed or self.rendering: return
        if self.pending:
            GLib.source_remove(self.pending)
            self.pending = 0
        self.rendering = True
        self.generation += 1
        generation = self.generation
        position = (self.scroll.get_hadjustment().get_value(),self.scroll.get_vadjustment().get_value())
        duplicate = None
        try:
            self.status.set_text('Rendering full layer…')
            self.window.get_window().set_cursor(None) if self.window.get_window() else None
            duplicate = self.image.duplicate()
            # IDs change on duplication; locate the selected top-level layer by position.
            index = self.image.get_layers().index(self.layer)
            target = duplicate.get_layers()[index]
            self.original = self.to_pixbuf(target)
            nl_apply(duplicate, target, self.config)
            self.pixbuf = self.to_pixbuf(target)
            self.status.set_text('Automatic preview updated. Zoom and view position retained.')
            self.redraw()
            def restore_position():
                if not self.closed and self.generation==generation:
                    for adjustment,value in zip((self.scroll.get_hadjustment(),self.scroll.get_vadjustment()),position):
                        adjustment.set_value(max(adjustment.get_lower(),min(value,adjustment.get_upper()-adjustment.get_page_size())))
                return False
            GLib.idle_add(restore_position)
        except Exception as error:
            self.status.set_text(str(error))
        finally:
            if duplicate:
                duplicate.delete()
            self.rendering = False

    @staticmethod
    def to_pixbuf(layer):
        width, height = layer.get_width(), layer.get_height()
        if width * height > 40_000_000:
            raise ValueError('Preview is limited to 40 megapixels to bound memory use.')
        rect = Gegl.Rectangle.new(0, 0, width, height)
        pixels = layer.get_buffer().get(rect, 1.0, "R'G'B'A u8", Gegl.AbyssPolicy.NONE)
        return GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(pixels)),
                    GdkPixbuf.Colorspace.RGB, True, 8, width, height, width * 4)

    def fit(self, *_):
        if self.pixbuf:
            z = min(self.scroll.get_allocated_width() / self.pixbuf.get_width(),
                    self.scroll.get_allocated_height() / self.pixbuf.get_height())
            self.zoom.set_value(max(5, min(800, z * 95)))

    def redraw(self):
        if self.pixbuf:
            z = self.zoom.get_value() / 100
            self.canvas.set_size_request(max(1, int(self.pixbuf.get_width()*z)),
                                         max(1, int(self.pixbuf.get_height()*z)))
            self.canvas.queue_draw()

    def draw(self, widget, cr):
        if self.pixbuf:
            from gi.repository import Gdk
            cr.set_source_rgb(.25, .25, .25)
            cr.paint()
            z = self.zoom.get_value()/100
            cr.scale(z, z)
            Gdk.cairo_set_source_pixbuf(cr, self.original if self.before.get_active() else self.pixbuf, 0, 0)
            cr.paint()

def worker_job(job,raw,expected):
    import subprocess
    python = os.path.join(os.environ['LOCALAPPDATA'], 'Programs', 'Python', 'Python313', 'python.exe')
    worker = os.path.join(os.path.dirname(__file__), 'scale-worker.py')
    result = subprocess.run([python,worker,'--job',json.dumps(job)],
                            input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=subprocess.CREATE_NO_WINDOW, timeout=600)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8',errors='replace'))
    if len(result.stdout) != expected:
        raise RuntimeError('Scaling worker returned an invalid pixel buffer.')
    return result.stdout

def resize_pixels(pixels,width,height,algorithm,options=None):
    raw,sw,sh = pixels
    return worker_job({'operation':'resize','source-width':sw,'source-height':sh,
                       'width':width,'height':height,'algorithm':algorithm,'options':options or {}},raw,width*height*16)

@contextmanager
def scaling_cpu_budget():
    """Include GIMP's native geometry/mask scaling in the temporary CPU budget."""
    if os.name != 'nt':
        yield
        return
    import ctypes,subprocess
    python = os.path.join(os.environ['LOCALAPPDATA'],'Programs','Python','Python313','python.exe')
    worker = os.path.join(os.path.dirname(__file__),'scale-worker.py')
    info = json.loads(subprocess.check_output([python,worker,'--cpu-info'],creationflags=subprocess.CREATE_NO_WINDOW,timeout=30))
    api = ctypes.WinDLL('kernel32',use_last_error=True)
    api.OpenProcess.argtypes = [ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong]
    api.OpenProcess.restype = ctypes.c_void_p
    api.GetProcessAffinityMask.argtypes = [ctypes.c_void_p,ctypes.POINTER(ctypes.c_size_t),ctypes.POINTER(ctypes.c_size_t)]
    api.SetProcessAffinityMask.argtypes = [ctypes.c_void_p,ctypes.c_size_t]
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = api.OpenProcess(0x0600,False,Gimp.getpid())
    if not handle: raise ctypes.WinError(ctypes.get_last_error())
    original,system = ctypes.c_size_t(),ctypes.c_size_t()
    changed = False
    try:
        if not api.GetProcessAffinityMask(handle,ctypes.byref(original),ctypes.byref(system)):
            raise ctypes.WinError(ctypes.get_last_error())
        mask = original.value & info['affinity-mask']
        if not mask or not api.SetProcessAffinityMask(handle,mask):
            raise RuntimeError('Unable to limit native scaling to physical cores minus two')
        changed = True
        yield
    finally:
        try:
            if changed and not api.SetProcessAffinityMask(handle,original.value):
                raise RuntimeError('Unable to restore GIMP CPU affinity after scaling')
        finally:
            api.CloseHandle(handle)

def advanced_scale(image, layer, config, whole_image):
    width, height = config.get_property('width'), config.get_property('height')
    algorithm = config.get_property('algorithm')
    layers = image.get_layers() if whole_image else [layer]
    if not layers or any(not isinstance(x, Gimp.Layer) or x.is_group() or x.is_text_layer() for x in layers):
        raise ValueError('Advanced Scale currently supports raster layers; rasterize text and ungroup layers first.')
    if any(x.get_filters() for x in layers):
        raise ValueError('Merge non-destructive layer filters before advanced scaling.')
    if image.get_base_type() != Gimp.ImageBaseType.RGB:
        raise ValueError('Convert the image to RGB before using Advanced Scale.')
    if width*height*len(layers) > 40_000_000:
        raise ValueError('Output is limited to 40 million layer-pixels to bound memory use.')
    sources = []
    if sum(x.get_width()*x.get_height() for x in layers) > 40_000_000:
        raise ValueError('Source layers exceed the 40 million layer-pixel limit.')
    for x in layers:
        w, h = x.get_width(), x.get_height()
        if w*h > 40_000_000:
            raise ValueError('Each source layer must be at most 40 megapixels.')
        raw = x.get_buffer().get(Gegl.Rectangle.new(0,0,w,h), 1, "R'G'B'A float", Gegl.AbyssPolicy.NONE)
        sources.append((x, (bytes(raw),w,h)))
    # Work out native geometry on a duplicate, then render everything before editing the original.
    trial = image.duplicate()
    try:
        Gimp.context_push()
        Gimp.context_set_interpolation(Gimp.InterpolationType.NOHALO)
        if whole_image:
            if not trial.scale(width, height):
                raise RuntimeError('GIMP could not scale the image geometry.')
            dims = [(x.get_width(), x.get_height()) for x in trial.get_layers()]
        else:
            dims = [(width,height)]
        if sum(w*h for w,h in dims) > 40_000_000:
            raise ValueError('Scaled layers exceed the 40 million layer-pixel limit.')
        options = {key:config.get_property(key) for key in SCALE_OPTIONS}
        rendered = [(x,w,h,resize_pixels(data,w,h,algorithm,options)) for (x,data),(w,h) in zip(sources,dims)]
        image.undo_group_start()
        try:
            if whole_image:
                if not image.scale(width,height):
                    raise RuntimeError('Image scale failed.')
            else:
                if not layer.scale(width,height,False):
                    raise RuntimeError('Layer scale failed.')
            for x,w,h,raw in rendered:
                rect = Gegl.Rectangle.new(0,0,w,h)
                buffer = x.get_shadow_buffer()
                buffer.set(rect, "R'G'B'A float", raw)
                buffer.flush()
                x.merge_shadow(True)
                x.update(0,0,w,h)
        finally:
            image.undo_group_end()
    finally:
        Gimp.context_pop()
        trial.delete()

def run(procedure, run_mode, image, drawables, config, data):
    preview = None
    try:
        if len(drawables) != 1:
            raise ValueError('Select exactly one raster layer.')
        layer = drawables[0]
        is_nl = procedure.get_name() == 'python-fu-nl-filter-preview'
        if is_nl and (layer.is_group() or layer.get_parent() is not None):
            raise ValueError('NL preview currently requires a top-level raster layer.')
        if run_mode == Gimp.RunMode.INTERACTIVE:
            GimpUi.init('gimp-render-tools')
            dialog = GimpUi.ProcedureDialog(procedure=procedure, config=config)
            if is_nl:
                for key,lower,upper in (('alpha',0.,1.),('radius',1/3,1.),('edge-threshold',.005,.5)):
                    widget = dialog.get_scale_entry(key,1.)
                    widget.set_bounds(lower,upper,True)
                    widget.get_spin_button().set_digits(3)
                    widget.get_spin_button().set_increments(.01,.1)
            else:
                for key in ('mitchell-b','mitchell-c','kaiser-beta','gaussian-sigma','sharpen','sharpen-radius'):
                    dialog.get_scale_entry(key,1.)
            dialog.fill(None)
            if is_nl:
                def filter_controls(*_):
                    native = config.get_property('filter') in NATIVE_FILTERS
                    dialog.get_scale_entry('radius',1.).set_bounds(1/3,1. if native else 8.,True)
                    dialog.get_scale_entry('edge-threshold',1.).set_sensitive(config.get_property('filter')=='bilateral')
                config.connect('notify::filter',filter_controls)
                filter_controls()
                note = Gtk.Label(label='Type Alpha up to 4 or Radius up to 8. Native Radius > 1 repeats passes; extra filters use pixels.')
                note.set_line_wrap(True)
                note.set_max_width_chars(65)
                dialog.get_content_area().pack_start(note,False,False,6)
                note.show()
                preview = Preview(image, layer, config, dialog)
                button = Gtk.Button(label='Open Render / Zoom Preview')
                button.connect('clicked', lambda *_: preview.window.show_all())
                dialog.get_content_area().pack_start(button, False, False, 8)
                button.show()
            else:
                def scale_controls(*_):
                    algorithm = config.get_property('algorithm')
                    enabled = {'lobes':algorithm in ('custom-lanczos','kaiser'),
                               'mitchell-b':algorithm=='mitchell','mitchell-c':algorithm=='mitchell',
                               'kaiser-beta':algorithm=='kaiser','gaussian-sigma':algorithm=='gaussian',
                               'antialias':algorithm in ('custom-lanczos','mitchell','catmull-rom','bspline','kaiser','gaussian'),
                               'sharpen-radius':config.get_property('sharpen')>0}
                    for key,sensitive in enabled.items(): dialog.get_widget(key,GObject.TYPE_NONE).set_sensitive(sensitive)
                config.connect('notify::algorithm',scale_controls)
                config.connect('notify::sharpen',scale_controls)
                scale_controls()
                target = image if procedure.get_name().endswith('image') else layer
                config.set_property('width', target.get_width())
                config.set_property('height', target.get_height())
                ratio = target.get_width() / target.get_height()
                updating = [False]
                def sync_size(cfg, spec):
                    if updating[0] or not cfg.get_property('keep-aspect'):
                        return
                    updating[0] = True
                    try:
                        if spec.name == 'width':
                            cfg.set_property('height', max(1,min(65536,round(cfg.get_property('width')/ratio))))
                        else:
                            cfg.set_property('width', max(1,min(65536,round(cfg.get_property('height')*ratio))))
                    finally:
                        updating[0] = False
                config.connect('notify::width', sync_size)
                config.connect('notify::height', sync_size)
            accepted = dialog.run()
            if preview:
                preview.close()
            dialog.destroy()
            if not accepted:
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        if is_nl:
            nl_apply(image,layer,config)
        else:
            with scaling_cpu_budget():
                advanced_scale(image,layer,config,procedure.get_name().endswith('image'))
        Gimp.displays_flush()
        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
    except Exception as error:
        traceback.print_exc()
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error(str(error)))

class RenderTools(Gimp.PlugIn):
    def do_query_procedures(self):
        return ['python-fu-nl-filter-preview']

    def do_set_i18n(self, name):
        return False, None, None

    def do_create_procedure(self, name):
        Gegl.init(None)
        p = Gimp.ImageProcedure.new(self,name,Gimp.PDBProcType.PLUGIN,run,None)
        p.set_image_types('RGB*, GRAY*' if 'nl-filter' in name else 'RGB*')
        p.set_sensitivity_mask(Gimp.ProcedureSensitivityMask.DRAWABLE)
        p.set_attribution('Custom GIMP Render Tools','GPL-3.0-or-later','2026')
        if 'nl-filter' in name:
            p.set_menu_label('NL Filter — Zoom Preview…')
            p.add_menu_path('<Image>/Filters/Enhance')
            p.set_documentation('NL Filter with an automatic zoomable render preview','Automatic preview with retained zoom and pan. Native alpha above 1 extrapolates strength; native radius above 1 uses repeated passes. Extra filters use radius in pixels.',name)
            p.add_double_argument('alpha','_Alpha / strength','Type up to 4; native values above 1 extrapolate the filter correction',0,4,.3,FLAGS)
            p.add_double_argument('radius','_Radius','Type up to 8; above 1 native filters use repeated passes; extra filters use pixels',1/3,8,1/3,FLAGS)
            p.add_double_argument('edge-threshold','Bilateral edge _threshold','Colour-difference tolerance for bilateral smoothing only',.005,1.,.1,FLAGS)
            choices = Gimp.Choice.new()
            for i,(key,label) in enumerate([('alpha-trim','NL: Alpha trimmed mean'),('optimal-estimation','NL: Optimal estimation'),('edge-enhancement','NL: Edge enhancement'),('gaussian','Gaussian smoothing'),('bilateral','Bilateral edge-preserving smoothing'),('median','Median / impulse removal'),('trimmed-mean','Trimmed mean (20%)'),('unsharp','Unsharp mask'),('local-contrast','Local contrast enhancement')]):
                choices.add(key,i,label,label)
            p.add_choice_argument('filter','Filter','NL algorithm',choices,'alpha-trim',FLAGS)
        else:
            whole = name.endswith('image')
            p.set_menu_label('Advanced Scale Image…' if whole else 'Advanced Scale Layer…')
            p.add_menu_path('<Image>/Image' if whole else '<Image>/Layer')
            p.set_documentation('Advanced alpha-aware raster resampling','Width and height are independent. Float processing; image geometry and masks use native NoHalo. Raster RGB layers only.',name)
            p.add_int_argument('width','Width (pixels)','Target width',1,65536,1,FLAGS)
            p.add_int_argument('height','Height (pixels)','Target height',1,65536,1,FLAGS)
            p.add_boolean_argument('keep-aspect','Keep aspect ratio','Link dimensions to the original aspect ratio in this dialog',True,FLAGS)
            choices = Gimp.Choice.new()
            for i,(key,label) in enumerate([('lanczos','Lanczos 3'),('staged-lanczos','Staged Box + Lanczos 3'),('linear-lanczos','Linear-light Lanczos 3'),('hamming','Hamming'),('box','Box / area averaging'),('custom-lanczos','Lanczos — adjustable lobes'),('mitchell','Mitchell–Netravali — adjustable B/C'),('catmull-rom','Catmull–Rom cubic'),('bspline','Cubic B-spline'),('kaiser','Kaiser-windowed sinc — adjustable'),('gaussian','Gaussian — adjustable sigma'),('bicubic','Bicubic'),('nearest','Nearest neighbour / pixel art')]):
                choices.add(key,i,label,label)
            p.add_choice_argument('algorithm','Algorithm','Resampling method',choices,'lanczos',FLAGS)
            p.add_int_argument('lobes','Sinc lobes','Support radius for adjustable Lanczos and Kaiser',2,8,3,FLAGS)
            p.add_double_argument('mitchell-b','Mitchell B','B=1/3, C=1/3 is the standard Mitchell–Netravali kernel',0,1,1/3,FLAGS)
            p.add_double_argument('mitchell-c','Mitchell C','B=0, C=0.5 gives Catmull–Rom',0,1,1/3,FLAGS)
            p.add_double_argument('kaiser-beta','Kaiser beta','Window shape for Kaiser sinc; higher is a stronger taper',0,16,6.5,FLAGS)
            p.add_double_argument('gaussian-sigma','Gaussian sigma','Gaussian resampling kernel width',.2,2,.65,FLAGS)
            p.add_boolean_argument('antialias','Antialias custom kernels','Widen custom kernel support when reducing images',True,FLAGS)
            p.add_boolean_argument('linear-light','Linear-light processing','Process any chosen algorithm in linear light',False,FLAGS)
            p.add_double_argument('sharpen','Output sharpening','Optional unsharp amount after resizing; 0 disables',0,2,0,FLAGS)
            p.add_double_argument('sharpen-radius','Sharpen radius','Output-pixel sigma for optional sharpening',.2,5,1,FLAGS)
        return p

if __name__ == '__main__':
    Gimp.main(RenderTools.__gtype__, sys.argv)
