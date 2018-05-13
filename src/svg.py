import datetime
import logging
import os
import pty
import pyte
import pyte.screens
import svgwrite.animate
import svgwrite.container
import svgwrite.path
import svgwrite.shapes
import svgwrite.text
import selectors
import sys
import tty
from typing import Dict, Tuple, Generator
from Xlib import display, rdb, Xatom
from Xlib.error import DisplayError

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

"""
Use case: Record a TERMINAL SESSION and RENDER it as an SVG ANIMATION. The idea
is to produce a short (<2 minutes) animation that can be showcased on a project page to
illustrate a use case.

RECORD a TERMINAL SESSION: CAPTURE input from the terminal session and save it together with both 
TIMINGS (when key are pressed or output is written to screen) and CONFIGURATION (how are 
colors rendered by the terminal, bold... etc). All this data will be used to replay the 
terminal session (since we captured the input of the session, not the output).

Once the terminal session has been replayed, it can be CONVERTED FRAME by frame to an 
SVG ANIMATION that mimicks the terminal session.

The terminal session should be SAVED so that it can be replayed and rendered with different
options at any time.

"""


class TerminalSession:
    """
    Record, save and replay a terminal session
    """
    def __init__(self):
        self.buffer_size = 1024
        self.colors = None

    def record(self, input_fileno: int=sys.stdin.fileno(), output_fileno: int=sys.stdout.fileno()) \
            -> Generator[Tuple[bytes, datetime.datetime], None, int]:
        """Record raw input and output of a shell session

        This function forks the current process. The child process is a shell which is a session
        leader, has a controlling terminal and is run in the background. The parent process, which
        runs in the foreground, transmits data between the standard input, output and the shell
        process and logs it. From the user point of view, it appears they are communicating with
        their shell (through their terminal emulator) when in fact they communicate with our parent
        process which logs all the data exchanged with the shell.

        Alternative file descriptors (filenos) can be passed to the function in replacement of
        the descriptors for the standard input and output

        The implementation of this method is mostly copied from the pty.spawn function of the
        CPython standard library. It has been modified in order to make the record function a
        generator.

        :param input_fileno: File descriptor of the input data stream
        :param output_fileno: File descriptor of the output data stream
        """
        shell = os.environ.get('SHELL', 'sh')

        pid, master_fd = pty.fork()
        if pid == 0:
            # Child process
            os.execlp(shell, shell)

        try:
            mode = tty.tcgetattr(input_fileno)
            tty.setraw(input_fileno)
        except tty.error:
            mode = None

        # Parent process
        sel = selectors.DefaultSelector()
        sel.register(master_fd, selectors.EVENT_READ)
        sel.register(input_fileno, selectors.EVENT_READ)

        while {master_fd, input_fileno} <= set(sel.get_map().keys()):
            events = sel.select()
            for key, _ in events:
                try:
                    data = os.read(key.fileobj, self.buffer_size)
                except OSError:
                    sel.unregister(key.fileobj)
                    break

                if not data:
                    sel.unregister(key.fileobj)
                    continue

                yield data, datetime.datetime.now()

                if key.fileobj == input_fileno:
                    while data:
                        n = os.write(master_fd, data)
                        data = data[n:]
                elif key.fileobj == master_fd:
                    os.write(output_fileno, data)

        if mode is not None:
            tty.tcsetattr(input_fileno, tty.TCSAFLUSH, mode)

        os.close(master_fd)
        return os.waitpid(pid, 0)[1]

    def replay(self):
        """
        From the data gathered during a terminal record session, render the screen at each
        step of the session
        :return:
        """
        pass

    def get_configuration(self):
        """Get configuration information related to terminal output rendering"""
        xresources_str = self._get_xresources()
        self.colors = self._parse_xresources(xresources_str)

    @staticmethod
    def _get_xresources() -> str:
        """Query the X server about the color configuration for the default display (Xresources)

        :return: Xresources as a string
        """
        try:
            d = display.Display()
            data = d.screen(0).root.get_full_property(Xatom.RESOURCE_MANAGER,
                                                      Xatom.STRING)
        except DisplayError as e:
            logger.debug(f'No color configuration could be gathered from the X display: {e}')
        else:
            if data:
                return data.value.decode('utf-8')
        return ''

    @staticmethod
    def _parse_xresources(xresources: str) -> Dict[str, str]:
        """Parse the Xresources string and return a mapping between colors and their value

        :return: dictionary mapping the name of each color to its hexadecimal value ('#abcdef')
        """
        res_db = rdb.ResourceDB(string=xresources)

        mapping = {}
        names = ['foreground', 'background'] + [f'color{index}' for index in range(16)]
        for name in names:
            res_name = 'Svg.' + name
            res_class = res_name
            try:
                mapping[name] = res_db[res_name, res_class]
            except KeyError:
                pass

        return mapping


class AsciiAnimation:
    """
    Feed on a set of ASCII screens and convert them to SVG frames
    """
    def __init__(self):
        pass

    def _render_frame_bg(self):
        pass

    def _render_frame_fg(self):
        pass

    def _render_frame(self):
        pass

    def render(self):
        pass


def group_by_time(timings, threshold=datetime.timedelta(milliseconds=50)):
    grouped_timings = []
    current_string = []
    current_time = None
    for character, t in timings:
        if current_time is not None:
            assert t - current_time >= datetime.timedelta(seconds=0)
            if t - current_time > threshold:
                # Flush current string
                s = b''.join(current_string)
                grouped_timings.append((s, current_time))
                current_string = []
                current_time = t
        else:
            current_time = t

        current_string.append(character)

    if current_string:
        grouped_timings.append((b''.join(current_string), current_time))

    return grouped_timings


def serialize_css_dict(css: Dict[str, Dict[str, str]]) -> str:
    def serialize_css_item(item: Dict[str, str]) -> str:
        return '; '.join(f'{prop}: {item[prop]}' for prop in item)

    items = [f'{item} {{{serialize_css_item(css[item])}}}' for item in css]
    return os.linesep.join(items)


def render_animation(timings, filename, end_pause=1):
    if end_pause < 0:
        raise ValueError(f'Invalid end_pause (must be >= 0): "{end_pause}"')

    font_size = 14
    # color_conf = get_Xresources_colors()
    color_conf = {}
    css = {
        # Apply this style to each and every element since we are using coordinates that depend on
        # the size of the font
        '*': {
            'font-family': '"DejaVu Sans Mono", monospace',
            'font-style': 'normal',
            'font-size': f'{font_size}px',
        },
        'text': {
            'fill': color_conf['foreground']
        },
        '.bold': {
            'font-weight': 'bold'
        }
    }
    css_ansi_colors = {f'.{color}': {'fill': color_conf[color]} for color in color_conf}
    css.update(css_ansi_colors)

    dwg = svgwrite.Drawing(filename, ('80ex', '28em'), debug=True)
    dwg.defs.add(dwg.style(serialize_css_dict(css)))
    args = {
        'insert': (0, 0),
        'size': ('100%', '100%'),
        'class': 'background'
    }
    r = svgwrite.shapes.Rect(**args)
    dwg.add(r)
    input_data, times = zip(*timings)

    screen = pyte.Screen(80, 24)
    stream = pyte.ByteStream(screen)
    first_animation_begin = f'0s; animation_{len(input_data)-1}.end'
    # Lien_height in 'em' unit
    line_height = 1.10
    for index, bs in enumerate(input_data):
        stream.feed(bs)
        frame = svgwrite.container.Group(id=f'frame_{index}', display='none')

        # Background
        frame_bg = draw_bg(screen.buffer, line_height, f'frame_bg_{index}')
        frame.add(frame_bg)

        # Foreground
        frame_fg = draw_fg(screen.buffer, line_height, f'frame_fg_{index}')
        frame.add(frame_fg)

        # Animation
        try:
            frame_duration = (times[index+1] - times[index]).total_seconds()
        except IndexError:
            frame_duration = end_pause

        assert frame_duration > 0
        extra = {
            'id': f'animation_{index}',
            'begin': f'animation_{index-1}.end' if index > 0 else first_animation_begin,
            'dur': f'{frame_duration:.3f}s',
            'values': 'inline;inline',
            'keyTimes': '0.0;1.0',
            'fill': 'remove'
        }
        frame.add(svgwrite.animate.Animate('display', **extra))
        dwg.add(frame)

    dwg.save()


# TODO: Merge rectangles over multiple lines
def draw_bg(screen_buffer, line_height, group_id):
    frame = svgwrite.container.Group(id=group_id)
    for row in screen_buffer.keys():
        last_bg_color = None
        start_col = 0
        last_col = None
        for col in sorted(screen_buffer[row]):
            char = screen_buffer[row][col]
            bg_color = char.fg if char.reverse else char.bg
            if bg_color == 'default':
                if char.reverse:
                    bg_color = 'foreground'
                else:
                    bg_color = 'background'

            if bg_color != last_bg_color or (last_col is not None and col != last_col + 1):
                if last_bg_color is not None and last_bg_color != 'background':
                    args = {
                        'insert': (f'{start_col}ex', f'{row * line_height + 0.25:.2f}em'),
                        'size': (f'{col-start_col}ex', f'{line_height}em'),
                        'class': last_bg_color
                    }
                    r = svgwrite.shapes.Rect(**args)
                    frame.add(r)
                start_col = col
            last_bg_color = bg_color
            last_col = col
        if screen_buffer[row] and last_bg_color is not None and last_bg_color != 'background':
            col = max(screen_buffer[row]) + 1
            args = {
                'insert': (f'{start_col}ex', f'{row * line_height + 0.25:.2f}em'),
                'size': (f'{col-start_col}ex', f'{line_height}em'),
                'class': last_bg_color
            }
            r = svgwrite.shapes.Rect(**args)
            frame.add(r)

    return frame


def draw_fg(screen_buffer, line_height, group_id):
    frame = svgwrite.container.Group(id=group_id)
    for row in screen_buffer:
        height = (row + 1) * line_height
        content = ''
        last_text_attributes = {}
        current_text_position = 0
        last_col = -1

        for col in sorted(screen_buffer[row]):
            char = screen_buffer[row][col]
            # Replace spaces with non breaking spaces so that consecutive spaces
            # are preserved
            data = char.data if char.data != ' ' else u'\u00A0'
            text_attributes = {}

            fg_color = char.bg if char.reverse else char.fg
            if fg_color == 'default' and char.reverse:
                fg_color = 'background'
            classes = []
            if fg_color != 'default':
                classes.append(fg_color)
            if char.bold:
                classes.append('bold')
            if classes:
                text_attributes['class'] = ' '.join(classes)

            if text_attributes != last_text_attributes or col != last_col + 1:
                if content:
                    text = svgwrite.text.Text(text=content,
                                              x=[f'{current_text_position + i}ex' for i in
                                                 range(len(content))],
                                              y=[f'{height:.2f}em'],
                                              **last_text_attributes)
                    frame.add(text)
                content = ''
                current_text_position = col

            content += data
            last_text_attributes = text_attributes
            last_col = col

        if content:
            text = svgwrite.text.Text(text=content,
                                      x=[f'{current_text_position + i}ex' for i in
                                         range(len(content))],
                                      y=[f'{height:.2f}em'],
                                      **last_text_attributes)
            frame.add(text)
    return frame
