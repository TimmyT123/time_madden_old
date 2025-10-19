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
    n = 0
    for i in sched:
        n += 1
        i = i[:-1]
        if 'week' in i.lower() or len(i) < 3:
            continue
        new_teams = i.split(',')
        for team in new_teams:
            team = team + '\n'
            if team in NFL_Teams:
                # print(f'{team} is good')
                pass
            else:
                print(f'{team} at line #{n} is NOT good.....................Boom!')
                quit()


def space_between_uu_and_uc_games(games_text):
    first = False
    games_lst = games_text.split('\n')

    for i, game in enumerate(games_lst):
        if 'week'.lower() in game.lower():
            first = False

        if '(' not in game:  # this makes sure game has a user or cpu in it
            continue

        try:
            pattern2 = r'^\w*\(U\)$'
            m2 = re.match(pattern2, game)
            if m2:
                games_lst[i] += ' - BYE'  # Add BYE to single teams

            pattern = r'\w*\((\w)\) vs \w*\((\w)'
            m = re.match(pattern, game).groups()
            if m[0] != m[1] and not first:
                games_lst.insert(i, '\n')
                first = True
        except:
            continue

    # ADD @everybody to the front of each week
    space = 0
    games_lst_copy = games_lst.copy()
    for i, word in enumerate(games_lst_copy):
        if 'week'.lower() in word.lower():
            space += 1
            games_lst.insert(i+space-1, '@everyone')

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
        if '(U)' in game and 'vs' in game:
            pattern = r'(\w+\(U\)) vs (\w+\(U\))'
            match = re.match(pattern, game)
            if match:
                user_user_games.append(f"{match.group(1)}-{match.group(2)}".replace("(U)", "").lower())

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


    sched = read_sched()
    users = read_users()
    NFL_Teams = read_NFL_Teams()

    if WEEK != 'all'.lower():
        pattern_week_number = r'\w+ (\w+)'
        WEEK_LAST_NUMBER = re.match(pattern_week_number, WEEK).groups()
        WEEK_LAST_NUMBER_INT = int(WEEK_LAST_NUMBER[0])+1
        WEEK_NEXT = 'WEEK ' + str(WEEK_LAST_NUMBER_INT)

    for game in sched:
        if 'week' in game.lower():
            check_if_user_not_in_current_week_games(gamesTemp, users)
            gamesTemp.clear()  # clear gamesTemp for the next week
            gamesTemp.append('\n')
            gamesTemp.append(game.replace(',', ''))
            continue

        for user in users:
            user1 = user.strip('\n')
            if user1 in game:
                gamesTemp.append(game.replace(',', ' vs '))

    check_if_user_not_in_current_week_games(gamesTemp, users)
    games_txt = comp_or_user(gameDupTempList, users, NFL_Teams)
    games_txt = space_between_uu_and_uc_games(games_txt)

    gamesTemp.clear()
    gameDupTempList.clear()  # clear both of these global lists or it will keep growing the next time we use it
    gameDupTempList2.clear()

    if WEEK == 'all'.lower():
        games_txt = games_txt.replace("@everyone", "")  # take out all @everyone
        return games_txt

    the_week = games_txt.index(WEEK.upper())
    # try:
    #     the_week = games_txt.index(WEEK.upper())
    # except:
    #     return  # TODO change to RETURN

    if WEEK_LAST_NUMBER_INT == 19:
        the_next_week = -1
    else:
        the_next_week = games_txt.index(WEEK_NEXT.upper())-10

    games_txt = games_txt[the_week-10:the_next_week]  # displays only the week that we want

    user_user_games = get_user_user_games(games_txt)  # finding and saving the user-user games
    save_user_user_games(user_user_games)

    return games_txt


def main():
    sched = read_sched()
    NFL_Teams = read_NFL_Teams()
    check_all_teams_in_schedule(sched, NFL_Teams)
    print("All team names are good!")
    # Additional functionalities can be added here


if __name__ == "__main__":
    main()

