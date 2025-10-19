# RUN THIS FILE TO CHECK TO SEE IF THE WURD24SCHED TEAM NAMES ARE GOOD
import re
import datetime

gamesTemp = []
userCopy = []

gameDupTempList = []
gameDupTempList2 = []


pathSched = './wurd24sched.csv'
pathUsers = './wurd24users.csv'
pathNFL_Teams = './NFL_Teams.csv'


def read_sched():
    with open(pathSched, 'r') as file:
        return [line for line in file]


def read_users():
    with open(pathUsers, 'r') as file:
        return [line for line in file]


def read_NFL_Teams():
    with open(pathNFL_Teams, 'r') as file:
        return [line for line in file]


def check_all_teams_in_schedule(sched, NFL_Teams):
    for n, i in enumerate(sched, start=1):
        i = i.rstrip('\n')
        # skip header rows like "WEEK 1," or "PRE 1,"
        if re.match(r'^\s*(WEEK|PRE)\b', i, flags=re.IGNORECASE) or len(i) < 3:
            continue
        # i is "TeamA,TeamB"
        new_teams = i.split(',')
        for team in new_teams:
            team_line = (team.strip() + '\n')
            if team_line not in NFL_Teams:
                print(f'{team_line} at line #{n} is NOT good.....................Boom!')
                quit()


def space_between_uu_and_uc_games(games_text):
    first = False
    games_lst = games_text.split('\n')

    for i, game in enumerate(games_lst):
        # reset separator at any header
        if re.match(r'^\s*(WEEK\s+\d+|PRE\s+\d+)\s*$', game.strip(), flags=re.IGNORECASE):
            first = False

        # only lines that show a team marker
        if '(' not in game:
            continue

        try:
            # BYE for single-user entries like "Raiders(U)"
            if re.match(r'^.+\(U\)\s*$', game.strip()):
                games_lst[i] += ' - BYE'

            # Separate first UC from UU (robust team tokens)
            m = re.match(r'^.*?\((U|C)\)\s+vs\s+.*?\((U|C)\)', game.strip())
            if m:
                if m.group(1) != m.group(2) and not first:
                    games_lst.insert(i, '\n')
                    first = True
        except:
            continue

    # Insert @everyone above each header line (WEEK n or PRE n)
    space = 0
    games_lst_copy = games_lst.copy()
    for i, word in enumerate(games_lst_copy):
        if re.match(r'^\s*(WEEK\s+\d+|PRE\s+\d+)\s*$', word.strip(), flags=re.IGNORECASE):
            space += 1
            games_lst.insert(i + space - 1, '@everyone')

    games_lst = [game + '\n' for game in games_lst]  # put return in at the end of games

    games_text = ''.join(games_lst) # make into a string

    new_games_text = ''
    num = 0
    for char in games_text:
        if char == '\n':  # get rid of triple \n
            num += 1
            if num == 3:
                num = 0
                continue
            new_games_text += char
        else:
            num = 0
            new_games_text += char

    return new_games_text


def comp_or_user(games, users, NFL_Teams):
    team_txt = ''
    for team in NFL_Teams:
        if team in users:
            team_txt = team_txt + str(team.strip('\n') + '(U)\n')
        else:
            team_txt = team_txt + str(team.strip('\n') + '(C)\n')

    games_txt = ''.join(games)
    team_lst = team_txt.split('\n')

    for team_r in team_lst:
        games_txt = games_txt.replace(team_r[0:-3], team_r)

    return games_txt


def get_user_user_games(games_txt):
    user_user_games = []
    games_lst = games_txt.split('\n')

    for game in games_lst:
        # Match "Team(U) vs Team(U)" with spaces/numbers allowed
        m = re.match(r'^(?P<a>.+?)\s*\(U\)\s+vs\s+(?P<b>.+?)\s*\(U\)\s*$', game.strip())
        if m:
            a = m.group('a').strip().lower()
            b = m.group('b').strip().lower()
            user_user_games.append(f"{a}-{b}")
    return user_user_games


def save_user_user_games(user_user_games):
    with open('user_user_teams.txt', 'w') as f:
        for game in user_user_games:
            f.write(game + '\n')


def check_if_user_not_in_current_week_games(weekgames, users):
    if len(weekgames) < 2:
        return
    for weekgame in weekgames:  # get rid of duplicate games
        if weekgame not in gameDupTempList2:
            gameDupTempList2.append(weekgame)
        else:
            gameDupTempList2.remove(weekgame)
            gameDupTempList2.insert(2, weekgame)
            pass

    userCopy = users.copy()
    for user in users:  # find out if there are bye players in this week
        for i, w in enumerate(gameDupTempList2):
            if user.strip('\n') in w:
                userCopy.remove(user)
                break
    gameDupTempList2.extend(userCopy)
    gameDupTempList[:] = gameDupTempList + gameDupTempList2
    gameDupTempList2.clear()


def wurd_sched_main(WEEK):
    # normalize: handles "pre 1", "PRE 1", "PRE 1," etc.
    norm_week = (WEEK or "").strip().rstrip(",").upper()

    def _norm_token(s: str) -> str:
        return (s or "").strip().rstrip(",").upper()

    sched = read_sched()
    users = read_users()
    NFL_Teams = read_NFL_Teams()

    # build the weekly text; treat both WEEK and PRE as headers
    for game in sched:
        if re.match(r'^\s*(WEEK|PRE)\b', game, flags=re.IGNORECASE):
            check_if_user_not_in_current_week_games(gamesTemp, users)
            gamesTemp.clear()
            gamesTemp.append('\n')
            gamesTemp.append(game.replace(',', ''))  # keep header, drop trailing comma
            continue

        for user in users:
            user1 = user.strip('\n')
            if user1 in game:
                gamesTemp.append(game.replace(',', ' vs '))

    check_if_user_not_in_current_week_games(gamesTemp, users)
    games_txt = comp_or_user(gameDupTempList, users, NFL_Teams)
    games_txt = space_between_uu_and_uc_games(games_txt)

    gamesTemp.clear()
    gameDupTempList.clear()
    gameDupTempList2.clear()

    # "all" => return everything (no @everyone)
    if _norm_token(norm_week) == "ALL":
        return games_txt.replace("@everyone", "")

    # slice only the requested block (works for WEEK N and PRE N)
    lines = games_txt.splitlines()

    def _is_header(line: str) -> bool:
        return bool(re.match(r'^\s*(WEEK\s+\d+|PRE\s+\d+)\s*$', _norm_token(line)))

    # find the exact header line that matches norm_week (e.g., "PRE 1")
    try:
        start = next(i for i, ln in enumerate(lines) if _norm_token(ln) == norm_week)
    except StopIteration:
        raise ValueError(f"Week token not found: {norm_week}")

    # end is next header or EOF
    try:
        end = next(j for j in range(start + 1, len(lines)) if _is_header(lines[j]))
    except StopIteration:
        end = len(lines)

    slice_str = "\n".join(lines[start:end])

    # keep only header + matchup lines
    lines = [ln.rstrip() for ln in slice_str.splitlines()]
    out = []
    for ln in lines:
        if re.match(r'^\s*(WEEK\s+\d+|PRE\s+\d+)\s*$', ln, flags=re.IGNORECASE):
            out.append(ln)
        elif re.search(r'\bvs\b', ln, flags=re.IGNORECASE):
            out.append(ln)
    # optional: add @everyone at end (your code already handles this elsewhere; keep only if you want it here)
    # out.append("@everyone")

    slice_str = "\n".join(out).strip()

    # persist user–user pairings for forum creation
    user_user_games = get_user_user_games(slice_str)
    save_user_user_games(user_user_games)

    return slice_str


def main():
    sched = read_sched()
    NFL_Teams = read_NFL_Teams()
    check_all_teams_in_schedule(sched, NFL_Teams)
    print("All team names are good!")
    # Additional functionalities can be added here


if __name__ == "__main__":
    main()

