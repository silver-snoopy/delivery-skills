"""Invented histories for the DM's bounded current-sprint action view."""
import unittest
from datetime import date, timedelta

from ledger import metrics
from ledger.config import Notes, Person
from tests.fixture import Board
from tests.test_metrics import config

ADA, BEA, OUT = 'ada@vendor.example', 'bea@vendor.example', 'po@customer.example'
PEOPLE = [Person('Ada Vale', [ADA], 'ALFA', None, None),
          Person('Bea Reed', [BEA], 'ALFA', None, None)]
OLD, NOW = 'Proj\\Old', 'Proj\\Now'


def board():
    return Board().sprint('Old', '2026-10-19', '2026-10-30').sprint(
        'Now', '2026-11-02', '2026-11-20')


def item(b, i, when='2026-11-02 09:00', **kw):
    return b.item(i).at(when, iteration=NOW, assigned=ADA, **kw)


def closed(b, notes=None, people=PEOPLE):
    return metrics.team_sprint(b.cache(), config(), people, 'ALFA', b.sprints[0],
                               notes or Notes(), {})


def merged_wording_board():
    b = (Board().sprint('First', '2026-09-21', '2026-10-02')
         .sprint('Second', '2026-10-05', '2026-10-16')
         .sprint('Old', '2026-10-19', '2026-10-30')
         .sprint('Now', '2026-11-02', '2026-11-20'))
    b.item(7101).at('2026-09-21 09:00', iteration='Proj\\First', assigned=ADA).at(
        '2026-09-23 09:00', state='Active').at('2026-09-29 09:00', state='Ready for QA').at(
        '2026-10-05 09:00', iteration='Proj\\Second').at(
        '2026-10-19 09:00', iteration=OLD).at('2026-11-02 09:00', iteration=NOW)
    b.item(90).at('2026-10-19 09:00', iteration=OLD, assigned=ADA, state='Active').at(
        '2026-10-29 09:00', state='Closed')
    return b


class ActionTests(unittest.TestCase):
    def actions(self, b, today='2026-11-10', prior=None, running=True, people=PEOPLE, off=None,
                cfg=None):
        self.assertTrue(callable(getattr(metrics, 'team_actions', None)),
                        'team_actions must provide structured running and closed signals')
        return metrics.team_actions(b.cache(), cfg or config(), people, 'ALFA', prior,
                                    b.sprints[-1] if running else None,
                                    date.fromisoformat(today), off or {})

    def row(self, result, i):
        return next(r for r in result['rows'] if r['id'] == i)

    def test_merged_problem_states_each_fact_once_without_own_id(self):
        b = merged_wording_board()
        prior = metrics.team_sprint(b.cache(), config(), PEOPLE, 'ALFA', b.sprints[-2], Notes(), {})
        row = self.row(self.actions(b, prior=prior), 7101)
        self.assertEqual(row['problem'], 'waited in Ready for QA 29 working days; '
                         'carried over 3 sprints; 48 days old, squad P85 11 days; '
                         'no PO agreement recorded')
        self.assertNotIn('#7101', row['problem'])
        self.assertNotIn('.;', row['problem'])
        self.assertEqual(row['problem'].count('P85'), 1)
        self.assertEqual((row['level'], row['badge'], row['kinds'], row['age']),
                         ('red', 'Stuck', ['agreed', 'carry', 'old', 'waiting'], 48))

    def test_person_problems_omit_names_and_preserve_other_item_ids(self):
        b = board()
        for i in (7201, 7202, 7203, 7204):
            item(b, i, '2026-11-09 09:00', state='Active')
        people = [Person('Ada Vale', [ADA], 'ALFA', None, None),
                  Person('Bea Reed', [BEA], 'ALFA', None, None)]
        off = {'Bea Reed': {date(2026, 11, d): 1 for d in (16, 17, 18, 19, 20)}}
        rows = [r for r in self.actions(b, people=people, off=off)['rows'] if r['id'] is None]
        self.assertEqual([(r['person'], r['problem']) for r in rows], [
            ('Bea Reed', 'no open item, 4 available days left'),
            ('Ada Vale', '4 items in progress: #7201, #7202, #7203, #7204')])
        for row in rows:
            self.assertNotIn(row['person'], row['problem'])
            self.assertFalse(row['problem'].endswith('.'))

    def test_carry_agreement_fragments_preserve_explicit_po_answers(self):
        for agreement, reason, expected in [
                ('unknown', '', 'carried over, no PO agreement recorded'),
                ('unknown', 'ask tomorrow', 'carried over, PO agreement unknown'),
                ('not agreed', 'scope changed', 'carried over, not agreed with the PO')]:
            with self.subTest(agreement=agreement, reason=reason):
                b = board()
                b.item(1).at('2026-10-19 09:00', iteration=OLD, assigned=ADA)
                prior = closed(b, Notes(items={1: (agreement, reason)}))
                row = self.row(self.actions(b, prior=prior, running=False), 1)
                self.assertEqual(row['problem'], expected)

    def test_singular_counts_in_action_fragments(self):
        cases = [(['held'], '#1 is On Hold for 1 working days.', 'On Hold 1 working day'),
                 (['old'], "#1 is 1 days old, older than the squad's P85 of 0 days.",
                  '1 day old, squad P85 0 days'),
                 (['old'], "#1 is 2 days old, older than the squad's P85 of 1 days.",
                  '2 days old, squad P85 1 day'),
                 (['carry'], '#1 has been carried over for 1 sprints.', 'carried over 1 sprint'),
                 (['waiting'], '#1 has waited in Ready for QA for 1 working days.',
                  'waited in Ready for QA 1 working day'),
                 (['none'], 'Ada Vale has 1 available days left but no open item assigned.',
                  'no open item, 1 available day left')]
        for kinds, text, expected in cases:
            with self.subTest(kinds=kinds, text=text):
                self.assertEqual(metrics._action_problem(dict(id=None if kinds == ['none'] else 1,
                    person='Ada Vale', kinds=kinds, problem=text)), expected)
        self.assertEqual(metrics._action_problem(dict(id=1, person='Ada Vale', kinds=['carry'],
            problem="#1 has been carried over for 2 sprints and is 1 days old, older than the squad's P85 of 0 days.")),
            'carried over 2 sprints; 1 day old, squad P85 0 days')
        b = board()
        item(b, 1, state='On Hold', category='InProgress')
        self.assertEqual(self.row(self.actions(b, '2026-11-04'), 1)['problem'],
                         'On Hold 1 working day')

    def test_changed_agreement_template_keeps_detail_without_crashing(self):
        self.assertEqual(metrics._action_problem(dict(id=1, person='Ada Vale', kinds=['agreed'],
            problem='#1 was carried over pending a new PO decision.')),
            'carried over, pending a new PO decision')

    def test_shared_fragment_preserves_team_names(self):
        self.assertEqual(metrics._action_problem(dict(id=1, person='Ada Vale', kinds=['shared'],
            problem='#1 is counted for teams ALFA, BETA and GAMMA.')),
            'counted for teams ALFA, BETA and GAMMA')

    def test_done_rows_only_in_the_running_sprint(self):
        b = board()
        b.item(1).at('2026-10-19 09:00', iteration=OLD, assigned=ADA, state='Active').at(
            '2026-10-27 09:00', state='Closed')
        item(b, 2, state='Active').at('2026-11-04 09:00', state='Closed')
        prior = closed(b)
        running = self.actions(b, prior=prior)
        self.assertEqual([(r['id'], r['badge']) for r in running['rows'] if r['id']], [(2, 'Done')])
        self.assertFalse(any(r['badge'] == 'Done' for r in
                             self.actions(b, '2026-10-31', prior=prior, running=False)['rows']))

    def test_merged_age_problems_use_the_same_age_as_the_now_column(self):
        b = board()
        b.item(1).at('2026-10-19 09:00', iteration=OLD, assigned=ADA, state='Active')
        b.item(2).at('2026-10-19 09:00', iteration=OLD, assigned=ADA, state='Active').at(
            '2026-10-20 09:00', state='Closed')
        prior = closed(b)
        row = self.row(self.actions(b, prior=prior), 1)
        import re
        ages = re.findall(r'(\d+) days old', row['problem'])
        self.assertEqual(set(ages), {str(row['age'])}, row['problem'])

    def test_running_figures_and_no_carry_or_rag(self):
        b = board()
        item(b, 1, state='Active').at('2026-11-06 09:00', state='Closed')
        item(b, 2).at('2026-11-05 09:00', iteration='Proj')
        item(b, 3, '2026-11-04 09:00', state='Active')
        item(b, 4)
        item(b, 5).at('2026-11-05 09:00', state='Removed')
        result = self.actions(b)
        self.assertEqual(result['current'], {
            'sprint': b.sprints[-1], 'day': 6, 'days': 15, 'planned': [1, 2, 4, 5],
            'delivered_planned': [1], 'added': [3], 'removed': [2, 5], 'delivered': [1],
            'held': [], 'waiting': []})
        self.assertNotIn('status', result)
        self.assertNotIn('status', result['current'])
        self.assertTrue(all(not r['carry'] for r in result['rows']))
        self.assertEqual(self.row(result, 3)['badge'], 'On track')
        self.assertEqual(self.row(result, 4)['badge'], 'Not started')
        done = self.row(result, 1)
        self.assertEqual((done['badge'], done['level'], done['open'], done['age']),
                         ('Done', 'green', False, None))
        self.assertEqual([r['id'] for r in result['rows']][-1], 1)  # Done rows come last
        self.assertNotIn(5, [r['id'] for r in result['rows']])  # removed: no Done row
        self.assertEqual(set(self.row(result, 4)), {'id', 'person', 'title', 'sprint',
                         'sprint_path', 'level', 'badge', 'kinds', 'problem', 'ask',
                         'age', 'open', 'carry'})

    def test_wait_thresholds_and_blocked_tag(self):
        for today, badge, flagged in [('2026-11-06', 'On track', False),
                                      ('2026-11-07', 'Waiting', True),
                                      ('2026-11-17', 'Waiting', True),
                                      ('2026-11-18', 'Stuck', True)]:
            with self.subTest(today=today):
                b = board()
                item(b, 1, state='Ready for QA')
                item(b, 2, tags=['bLoCkEd'], state='Active')
                a = self.actions(b, today)
                self.assertEqual(self.row(a, 1)['badge'], badge)
                if flagged:
                    days = {'2026-11-07': 4, '2026-11-17': 10, '2026-11-18': 11}[today]
                    self.assertEqual(self.row(a, 2)['problem'],
                                     'waited with the Blocked tag %d working days' % days)
                self.assertEqual('waiting' in self.row(a, 2)['kinds'], flagged)
                self.assertEqual(a['current']['waiting'], [1, 2])
                if badge == 'Stuck':
                    self.assertEqual(self.row(a, 1)['level'], 'red')

    def test_hold_any_duration_and_over_ten_days(self):
        for today, badge in [('2026-11-03', 'On Hold'), ('2026-11-17', 'On Hold'),
                             ('2026-11-18', 'Stuck')]:
            with self.subTest(today=today):
                b = board()
                item(b, 1, state='On Hold', category='InProgress')
                a = self.actions(b, today)
                self.assertEqual(self.row(a, 1)['badge'], badge)
                self.assertIn('held', self.row(a, 1)['kinds'])
                days = {'2026-11-03': 0, '2026-11-17': 10, '2026-11-18': 11}[today]
                self.assertEqual(self.row(a, 1)['problem'], 'On Hold %d working days' % days)
                self.assertEqual(a['current']['held'], [1])

    def test_stale_equality_and_child_changes(self):
        b = board()
        item(b, 1, state='Active')
        item(b, 2, state='Active')
        b.item(20, 'Task', parent=2).at('2026-11-06 09:00', assigned=ADA, state='Active')
        self.assertNotIn('stale', self.row(self.actions(b, '2026-11-07'), 1)['kinds'])
        a = self.actions(b, '2026-11-10')
        self.assertEqual(self.row(a, 1)['badge'], 'No movement')
        self.assertEqual(self.row(a, 2)['badge'], 'On track')

    def test_not_started_after_third_working_day_and_weekends(self):
        b = board()
        item(b, 1)
        for today, day, badge in [('2026-11-02', 0, None), ('2026-11-04', 2, 'On track'),
                                  ('2026-11-05', 3, 'Not started'),
                                  ('2026-11-06', 4, 'Not started'),
                                  ('2026-11-07', 5, 'Not started'),
                                  ('2026-11-09', 5, 'Not started')]:
            a = self.actions(b, today)
            self.assertEqual(a['current']['day'], day)
            self.assertEqual(a['current']['planned'], [] if day == 0 else [1])
            if badge:
                self.assertEqual(self.row(a, 1)['badge'], badge)
        # A pre-existing commitment is visible on the first day, without future first-day edits.
        b = board()
        item(b, 1, '2026-11-01 09:00')
        item(b, 2, '2026-11-02 09:00')
        self.assertEqual(self.actions(b, '2026-11-02')['current']['planned'], [1])

    def test_midnight_isolation_including_future_activation_close_and_reassignment(self):
        b = board()
        a = item(b, 1, state='Active')
        p = item(b, 2)
        check = item(b, 3).at('2026-11-06 09:00', state='Closed')
        expected = self.actions(b)
        a.at('2026-11-10 00:01', state='Closed', assigned=BEA)
        p.at('2026-11-10 08:00', state='Active', assigned=OUT)
        check.at('2026-11-10 08:00', state='Active')
        item(b, 4, '2026-11-10 09:00', state='Active')
        self.assertEqual(self.actions(b), expected)
        self.assertEqual(self.row(expected, 3)['badge'], 'Check')
        self.assertIsNone(self.row(expected, 3)['age'])

    def test_grey_reopened_check_and_age(self):
        b = board()
        item(b, 1, state='Active').at('2026-11-04 09:00', state='Closed').at(
            '2026-11-09 09:00', state='Active')
        a = self.actions(b)
        self.assertEqual((self.row(a, 1)['badge'], self.row(a, 1)['kinds'],
                          self.row(a, 1)['age'], self.row(a, 1)['open']),
                         ('Check', ['reopened'], 8, True))

    def test_no_work_proposed_leave_onboarding_and_remaining_days(self):
        b = board()
        item(b, 1)  # Proposed work suppresses No work, even before it starts.
        people = PEOPLE + [Person('Cy Lake', ['cy@vendor.example'], 'ALFA', date(2026, 11, 2), None)]
        off = {'Bea Reed': {date(2026, 11, 10) + timedelta(days=i): 1 for i in range(11)}}
        a = self.actions(b, people=people, off=off)
        self.assertFalse([r for r in a['rows'] if r['id'] is None])
        bea = next(p for p in a['people'] if p['name'] == 'Bea Reed')
        self.assertEqual((bea['nominal'], bea['available']), (15, 6))
        self.assertTrue(next(p for p in a['people'] if p['name'] == 'Cy Lake')['onboarding'])
        a = self.actions(b)
        no_work = next(r for r in a['rows'] if r['id'] is None)
        self.assertEqual((no_work['person'], no_work['level'], no_work['badge']),
                         ('Bea Reed', 'red', 'No work'))
        self.assertFalse([r for r in self.actions(b, '2026-11-20', off={
            'Bea Reed': {date(2026, 11, 20): 1}})['rows'] if r['badge'] == 'No work'])

    def test_wip_only_over_three(self):
        b = board()
        for i in range(1, 5):
            item(b, i, '2026-11-09 09:00', state='Active')
        for i in range(5, 8):
            b.item(i).at('2026-11-09 09:00', iteration=NOW, assigned=BEA, state='Active')
        rows = [r for r in self.actions(b)['rows'] if r['badge'] == 'Too much WIP']
        self.assertEqual([(r['person'], r['level'], r['id']) for r in rows],
                         [('Ada Vale', 'amber', None)])

    def test_owners_current_child_recent_child_and_history(self):
        b = board()
        direct = item(b, 1, state='Active').at('2026-11-05 09:00', assigned=BEA)
        b.item(10, 'Task', parent=1).at('2026-11-06 09:00', assigned=ADA, state='Active')
        for i in (2, 3, 4):
            b.item(i).at('2026-11-02 09:00', iteration=NOW, assigned=OUT, state='Active')
        b.item(20, 'Task', parent=2).at('2026-11-03 09:00', assigned=ADA, state='Active')
        b.item(21, 'Task', parent=2).at('2026-11-05 09:00', assigned=BEA, state='Active')
        b.item(22, 'Task', parent=2).at('2026-11-06 09:00', assigned=ADA, state='Closed')
        b.item(30, 'Task', parent=3).at('2026-11-03 09:00', assigned=ADA, state='Active').at(
            '2026-11-05 09:00', assigned=BEA).at('2026-11-06 09:00', assigned=OUT)
        # A closed child cannot take ownership from an earlier ours history owner.
        b.item(40, 'Task', parent=4).at('2026-11-03 09:00', assigned=ADA, state='Active').at(
            '2026-11-06 09:00', state='Closed', assigned=OUT)
        a = self.actions(b)
        self.assertEqual([self.row(a, i)['person'] for i in (1, 2, 3, 4)],
                         ['Bea Reed', 'Bea Reed', 'Bea Reed', 'Ada Vale'])
        direct.at('2026-11-09 09:00', assigned=ADA)
        self.assertEqual(self.row(self.actions(b), 1)['person'], 'Ada Vale')

    def test_child_activity_prevents_not_started_and_no_work(self):
        b = board()
        b.item(1).at('2026-11-02 09:00', iteration=NOW, assigned=OUT)
        b.item(2, 'Task', parent=1).at('2026-11-02 09:00', assigned=ADA, state='Active')
        a = self.actions(b, people=PEOPLE[:1])
        self.assertEqual(self.row(a, 1)['badge'], 'No movement')
        self.assertNotIn('not_started', self.row(a, 1)['kinds'])
        self.assertEqual(len(a['rows']), 1)

    def test_closed_window_p85_chase_and_carried_in_stuck(self):
        b = board()
        b.item(90).at('2026-10-19 09:00', iteration=OLD, assigned=ADA, state='Active').at(
            '2026-10-20 09:00', state='Closed')
        b.item(1).at('2026-10-19 09:00', iteration=OLD, assigned=ADA, state='Active').at(
            '2026-11-02 09:00', iteration=NOW)
        item(b, 2, state='Active').at('2026-11-09 09:00')
        # A running delivery must not raise the closed-window threshold.
        item(b, 3, state='Active').at('2026-11-09 09:00', state='Closed')
        prior = closed(b, Notes(items={1: ('agreed', 'split')}))
        a = self.actions(b, prior=prior)
        self.assertEqual((self.row(a, 1)['level'], self.row(a, 1)['badge']), ('red', 'Stuck'))
        self.assertEqual(self.row(a, 2)['badge'], 'Chase')
        self.assertTrue(self.row(a, 1)['carry'])
        self.assertEqual(self.row(a, 1)['sprint'], 'Now')
        self.assertEqual(self.row(a, 1)['sprint_path'], NOW)
        self.assertIn('; ', self.row(a, 1)['problem'])

    def test_old_added_item_carried_in_is_stuck_without_running_carry(self):
        b = board()
        b.item(90).at('2026-10-19 09:00', iteration=OLD, assigned=ADA, state='Active').at(
            '2026-10-20 09:00', state='Closed')
        b.item(1).at('2026-10-21 09:00', iteration=OLD, assigned=ADA, state='Active').at(
            '2026-11-02 09:00', iteration=NOW)
        prior = closed(b)
        self.assertEqual(prior['carry'], [])  # It was added after the commitment snapshot.
        r = self.row(self.actions(b, prior=prior), 1)
        self.assertEqual((r['level'], r['badge'], r['carry']), ('red', 'Stuck', False))

    def test_quiet_agreed_carry_keeps_membership_without_a_signal(self):
        b = board()
        b.item(1).at('2026-10-19 09:00', iteration=OLD, assigned=ADA).at(
            '2026-11-09 09:00', iteration=NOW, state='Active')
        prior = closed(b, Notes(items={1: ('agreed', 'continue')}))
        self.assertEqual([c['id'] for c in prior['carry']], [1])
        r = self.row(self.actions(b, prior=prior), 1)
        self.assertEqual((r['badge'], r['kinds'], r['carry']), ('On track', [], True))

    def test_earlier_outside_work_does_not_make_new_ours_item_stuck(self):
        b = board()
        b.item(90).at('2026-10-19 09:00', iteration=OLD, assigned=ADA, state='Active').at(
            '2026-10-20 09:00', state='Closed')
        b.item(1).at('2026-10-19 09:00', iteration=OLD, assigned=OUT, state='Active').at(
            '2026-11-09 09:00', iteration=NOW, assigned=ADA)
        r = self.row(self.actions(b, prior=closed(b)), 1)
        self.assertEqual((r['level'], r['badge']), ('amber', 'Chase'))

    def test_merge_unagreed_carry_and_retention_of_all_kinds(self):
        b = board()
        b.item(1).at('2026-10-19 09:00', iteration=OLD, assigned=ADA, state='Active').at(
            '2026-11-02 09:00', iteration=NOW, state='Ready for QA')
        a = self.actions(b, prior=closed(b))
        rows = [r for r in a['rows'] if r['id'] == 1]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['kinds'], ['agreed', 'waiting'])
        self.assertTrue(rows[0]['carry'])
        self.assertIn('; ', rows[0]['problem'])
        self.assertIn('Raise it with the PO.', rows[0]['ask'])
        self.assertIn('Confirm with the PO', rows[0]['ask'])

    def test_closed_only_maps_flags_and_retains_people_without_rows(self):
        b = board()
        b.item(1).at('2026-10-19 09:00', iteration=OLD, assigned=ADA, state='Active')
        b.item(2).at('2026-10-19 09:00', iteration=OLD, assigned=BEA).at(
            '2026-10-20 09:00', state='Closed')
        prior = closed(b)
        a = self.actions(b, prior=prior, running=False)
        self.assertIsNone(a['current'])
        self.assertEqual(self.row(a, 1)['badge'], 'Carry-over')
        self.assertEqual(self.row(a, 2)['badge'], 'Check')
        self.assertEqual(self.row(a, 1)['sprint'], 'Old')
        self.assertIn('No movement', [s['badge'] for s in prior['attention']])
        self.assertEqual([p['name'] for p in a['people']], ['Ada Vale', 'Bea Reed'])
        empty = self.actions(board(), running=False)
        self.assertEqual(empty, {'people': [], 'rows': [], 'current': None})
        # All working members survive even when no action belongs to them.
        prior['attention'] = []
        self.assertEqual(len(self.actions(b, prior=prior, running=False)['people']), 2)

    def test_no_rows_and_ok_share_the_final_name_order_group(self):
        b = board()
        b.item(1).at('2026-11-09 09:00', iteration=NOW, assigned=BEA, state='Active')
        people = [Person('Ada Vale', [ADA], 'ALFA', date(2026, 11, 2), None), PEOPLE[1]]
        a = self.actions(b, people=people)
        self.assertEqual([p['name'] for p in a['people']], ['Ada Vale', 'Bea Reed'])
        self.assertEqual([r['id'] for r in a['rows']], [1])

    def test_running_owner_history_parent_alias_and_task_level(self):
        b = board()
        item(b, 1, state='Active').at('2026-11-06 09:00', assigned=OUT)
        item(b, 2, state='Active').at('2026-11-06 09:00', assigned='ADA.ALT@vendor.example')
        people = [Person('Ada Vale', [ADA, 'ada.alt@vendor.example'], 'ALFA', None, None)]
        a = self.actions(b, people=people)
        self.assertEqual([self.row(a, i)['person'] for i in (1, 2)], ['Ada Vale', 'Ada Vale'])
        cfg = config()
        cfg.teams['ALFA']['count'] = 'tasks'
        b.item(3, 'Task').at('2026-11-02 09:00', iteration=NOW, assigned=ADA, state='Active')
        b.item(4, 'Task', parent=3).at('2026-11-09 09:00', assigned=BEA, state='Active')
        a = self.actions(b, cfg=cfg)
        self.assertEqual(self.row(a, 3)['person'], 'Ada Vale')
        self.assertEqual(self.row(a, 3)['badge'], 'No movement')
        self.assertEqual(self.row(a, 4)['person'], 'Bea Reed')
        self.assertEqual({r['id'] for r in a['rows']}, {3, 4})

    def test_running_join_leave_zero_allocation_and_no_future_capacity(self):
        b = board()
        people = [Person('Ada Vale', [ADA], 'ALFA', None, date(2026, 11, 6)),
                  Person('Bea Reed', [BEA], 'ALFA', None, None, 0),
                  Person('Cy Lake', ['cy@vendor.example'], 'ALFA', date(2026, 11, 23), None)]
        a = self.actions(b, people=people)
        self.assertEqual([p['name'] for p in a['people']], ['Ada Vale', 'Bea Reed'])
        self.assertEqual(a['rows'], [])

    def test_age_equal_to_p85_is_not_chase(self):
        b = board()
        b.item(90).at('2026-10-19 09:00', iteration=OLD, assigned=ADA, state='Active').at(
            '2026-10-20 09:00', state='Closed')
        item(b, 1, '2026-11-08 09:00', state='Active')
        item(b, 2, '2026-11-07 09:00', state='Active')
        a = self.actions(b, prior=closed(b))
        self.assertEqual((self.row(a, 1)['age'], self.row(a, 1)['badge']), (2, 'On track'))
        self.assertEqual((self.row(a, 2)['age'], self.row(a, 2)['badge']), (3, 'Chase'))

    def test_closed_only_all_badge_mappings_keep_information_and_asks(self):
        b = board()
        for i in range(1, 5):
            b.item(i).at('2026-10-23 09:00', iteration=OLD, assigned=ADA, state='Active')
        b.item(5).at('2026-10-23 09:00', iteration=OLD, assigned=ADA, state='Ready for QA')
        b.item(6).at('2026-10-19 09:00', iteration=OLD, assigned=ADA).at(
            '2026-10-20 09:00', state='Closed').at('2026-10-26 09:00', state='Active')
        people = PEOPLE + [Person('Cy Lake', ['cy@vendor.example'], 'ALFA', None, None)]
        # Give Bea an assigned, proposed item so she gets Nothing delivered, not No work.
        b.item(7).at('2026-10-26 09:00', iteration=OLD, assigned=BEA)
        prior = closed(b, people=people)
        a = self.actions(b, prior=prior, running=False, people=people)
        self.assertEqual(self.row(a, 1)['badge'], 'No movement')
        self.assertEqual(self.row(a, 5)['badge'], 'Waiting')
        self.assertEqual(self.row(a, 6)['badge'], 'Check')
        self.assertEqual({(r['person'], r['badge']) for r in a['rows'] if r['id'] is None},
                         {('Ada Vale', 'Too much WIP'), ('Bea Reed', 'Nothing delivered'),
                          ('Cy Lake', 'No work')})
        expected = {1: 'Active with no board change 5 working days',
                    2: 'Active with no board change 5 working days',
                    3: 'Active with no board change 5 working days',
                    4: 'Active with no board change 5 working days',
                    5: 'waited in Ready for QA 5 working days',
                    6: 'closed without ever being in progress; reopened',
                    'Ada Vale': '6 items in progress: #1, #2, #3, #4, #5, #6',
                    'Bea Reed': 'delivered nothing, 10 available days',
                    'Cy Lake': 'no item assigned during the sprint, 10 available days'}
        for signal in prior['attention']:
            r = next(r for r in a['rows'] if r['id'] == signal['id'] and (
                r['id'] is not None or r['person'] == signal['person'] and
                signal['kind'] in r['kinds']))
            self.assertEqual(r['problem'], expected[r['id'] or r['person']])
            self.assertIn(signal['ask'], r['ask'])

    def test_closed_carry_red_and_two_sprint_amber_mapping(self):
        b = Board().sprint('Earlier', '2026-10-05', '2026-10-16').sprint(
            'Old', '2026-10-19', '2026-10-30')
        b.item(1).at('2026-10-05 09:00', iteration='Proj\\Earlier', assigned=ADA,
                     state='Active').at('2026-10-19 09:00', iteration=OLD)
        prior = metrics.team_sprint(b.cache(), config(), PEOPLE, 'ALFA', b.sprints[-1],
                                    Notes(items={1: ('agreed', 'split')}), {})
        a = self.actions(b, prior=prior, running=False)
        self.assertEqual(self.row(a, 1)['badge'], 'Carry-over')
        b.item(90).at('2026-10-06 09:00', iteration='Proj\\Earlier', assigned=BEA,
                      state='Active').at('2026-10-07 09:00', state='Closed')
        prior = metrics.team_sprint(b.cache(), config(), PEOPLE, 'ALFA', b.sprints[-1],
                                    Notes(items={1: ('agreed', 'split')}), {})
        a = self.actions(b, prior=prior, running=False)
        self.assertEqual((self.row(a, 1)['level'], self.row(a, 1)['badge']), ('red', 'Carry-over'))

    def test_severity_then_name_and_id_order(self):
        b = board()
        item(b, 9, '2026-11-09 09:00', state='Active')
        item(b, 5, state='Ready for QA')
        item(b, 2, state='Ready for QA')
        a = self.actions(b)
        self.assertEqual([p['name'] for p in a['people']], ['Bea Reed', 'Ada Vale'])
        self.assertEqual([r['id'] for r in a['rows']], [None, 2, 5, 9])


if __name__ == '__main__':
    unittest.main()
