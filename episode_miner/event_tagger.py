from __future__ import unicode_literals

from sys import version_info

import ahocorasick
import unicodecsv as csv
from estnltk.names import START, END
from pandas import DataFrame

TERM = 'term'
WSTART_RAW = 'wstart_raw'
WEND_RAW = 'wend_raw'
WSTART = 'wstart'
WEND = 'wend'
CSTART = 'cstart'

DEFAULT_METHOD = 'ahocorasick' if version_info.major >= 3 else 'naive'


class EventTagger(object):
    """A class that finds a list of events from Text object based on user-provided vocabulary. 
    The events are tagged by several metrics (start, end, cstart, wstart) 
    and user-provided classificators.
    """

    def __init__(self, event_vocabulary, search_method=DEFAULT_METHOD, conflict_resolving_strategy='MAX',
                 return_layer=False, layer_name='events'):
        """Initialize a new EventTagger instance.
        Parameters
        ----------
        event_vocabulary: str, pandas.DataFrame, list
            Vocabulary of events.
            If ``str`` creates event vocabulary from csv file ``event_vocabulary``
        search_method: 'naive', 'ahocorasic'
            Method to find events in text (default: 'naive' for python2 and 'ahocorasick' for python3).
        conflict_resolving_strategy: 'ALL', 'MAX', 'MIN'
            Strategy to choose between overlaping events (default: 'MAX').
        return_layer: bool
            if True, EventTagger.tag(text) returns a layer. If False, EventTagger.tag(text) annotates the text object with the layer.
        layer_name: str
            if return_layer is False, EventTagger.tag(text) annotates to this layer of the text object. Default 'events'
        """
        self.layer_name = layer_name
        self.return_layer = return_layer
        if search_method not in ['naive', 'ahocorasick']:
            raise ValueError("Unknown search_method '%s'." % search_method)
        if conflict_resolving_strategy not in ['ALL', 'MIN', 'MAX']:
            raise ValueError("Unknown onflict_resolving_strategy '%s'." % conflict_resolving_strategy)
        if search_method == 'ahocorasick' and version_info.major < 3:
            raise ValueError(
                    "search_method='ahocorasick' is not supported by Python %s. Try 'naive' instead." % version_info.major)
        self.event_vocabulary = self.__read_event_vocabulary(event_vocabulary)
        self.search_method = search_method
        self.ahocorasick_automaton = None
        self.conflict_resolving_strategy = conflict_resolving_strategy

    @staticmethod
    def __read_event_vocabulary(event_vocabulary):
        if isinstance(event_vocabulary, list):
            event_vocabulary = event_vocabulary
        elif isinstance(event_vocabulary, DataFrame):
            event_vocabulary = event_vocabulary.to_dict('records')
        elif isinstance(event_vocabulary, str):
            with open(event_vocabulary, 'rb') as file:
                reader = csv.DictReader(file)
                event_vocabulary = []
                for row in reader:
                    event_vocabulary.append(row)
        else:
            raise TypeError("%s not supported as event_vocabulary" % type(event_vocabulary))
        if len(event_vocabulary) == 0:
            return []
        if (START in event_vocabulary[0] or
                    END in event_vocabulary[0] or
                    WSTART in event_vocabulary[0] or
                    WEND in event_vocabulary[0] or
                    CSTART in event_vocabulary[0]):
            raise KeyError('Illegal key in event vocabulary.')
        if TERM not in event_vocabulary[0]:
            raise KeyError("Missing key '" + TERM + "' in event vocabulary.")
        return event_vocabulary

    def __find_events_naive(self, text):
        events = []
        for entry in self.event_vocabulary:
            start = text.find(entry[TERM])
            while start > -1:
                events.append(entry.copy())
                events[-1].update({START: start, END: start + len(entry[TERM])})
                start = text.find(entry[TERM], start + 1)
        return events

    def __find_events_ahocorasick(self, text):
        events = []
        if self.ahocorasick_automaton == None:
            self.ahocorasick_automaton = ahocorasick.Automaton()
            for entry in self.event_vocabulary:
                self.ahocorasick_automaton.add_word(entry[TERM], entry)
            self.ahocorasick_automaton.make_automaton()
        for item in self.ahocorasick_automaton.iter(text):
            events.append(item[1].copy())
            events[-1].update({START: item[0] + 1 - len(item[1][TERM]), END: item[0] + 1})
        return events

    def __resolve_conflicts(self, events):
        events.sort(key=lambda event: event[END])
        events.sort(key=lambda event: event[START])
        if self.conflict_resolving_strategy == 'ALL':
            return events
        elif self.conflict_resolving_strategy == 'MAX':
            if len(events) < 2:
                return events
            bookmark = 0
            while bookmark < len(events) - 1 and events[0][START] == events[bookmark + 1][START]:
                bookmark += 1
            new_events = [events[bookmark]]
            for i in range(bookmark + 1, len(events) - 1):
                if events[i][END] > new_events[-1][END] and events[i][START] != events[i + 1][START]:
                    new_events.append(events[i])
            if events[-1][END] > new_events[-1][END]:
                new_events.append(events[-1])
            return new_events
        elif self.conflict_resolving_strategy == 'MIN':
            if len(events) < 2:
                return events
            while len(events) > 1 and events[-1][START] == events[-2][START]:
                del events[-1]
            for i in range(len(events) - 2, 0, -1):
                if events[i][START] == events[i - 1][START] or events[i][END] >= events[i + 1][END]:
                    del events[i]
            if len(events) > 1 and events[0][END] >= events[1][END]:
                del events[0]
            return events


    def __event_intervals(self, events, text):
        bookmark = 0
        overlapping_events = False
        last_end = 0
        for event in events:
            if last_end > event[START]:
                overlapping_events = True
            last_end = event[END]
            event[WSTART_RAW] = len(text.word_spans)
            event[WEND_RAW] = 0
            for i in range(bookmark, len(text.word_spans) - 1):
                if text.word_spans[i][0] <= event[START] < text.word_spans[i + 1][0]:
                    event[WSTART_RAW] = i
                    bookmark = i
                if text.word_spans[i][0] < event[END] <= text.word_spans[i + 1][0]:
                    event[WEND_RAW] = i + 1
                    break
        if not overlapping_events:
            w_shift = 0
            c_shift = 0
            for event in events:
                event[WSTART] = event[WSTART_RAW] - w_shift
                w_shift += event[WEND_RAW] - event[WSTART_RAW] - 1

                event[CSTART] = event[START] - c_shift
                c_shift += event[END] - event[START] - 1
        return events

    def tag(self, text):
        """Retrieves list of events in text.
        
        Parameters
        ----------
        text: Text
            The text to search for events.
            
        Returns
        -------
        list of events sorted by start, end
        """
        if self.search_method == 'ahocorasick':
            events = self.__find_events_ahocorasick(text.text)
        elif self.search_method == 'naive':
            events = self.__find_events_naive(text.text)

        events = self.__resolve_conflicts(events)

        self.__event_intervals(events, text)

        if self.return_layer:
            return events
        else:
            text[self.layer_name] = events
