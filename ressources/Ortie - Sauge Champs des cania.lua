-- Trajet créer par AntoineOB--

--Sauge - Ortie Champs des cania --

-- Discord de ONLYBOT -- 

-- https://discord.gg/m4QYvAFQJx --

ELEMENTS_TO_GATHER = {254,255,84}
MAX_PODS = 90



function move()
    return {
        {map = "162791424", changeMap = "usezaap:142087694"}, -- -26,-42	
        {map = "142087694", changeMap = "bottom"}, -- -27,-36
        {map = "142087695", changeMap = "left", gather = true}, -- -27,-35
        {map = "142087183", changeMap = "left", gather = true}, -- -28,-35
        {map = "142086671", changeMap = "top", gather = true}, -- -29,-35
        {map = "142086670", changeMap = "top", gather = true}, -- -29,-36
        {map = "142086669", changeMap = "left", gather = true}, -- -29,-37
        {map = "142086157", changeMap = "top", gather = true}, -- -30,-37
        {map = "142086156", changeMap = "top", gather = true}, -- -30,-38
        {map = "142086155", changeMap = "right", gather = true}, -- -30,-39
        {map = "142086667", changeMap = "right", gather = true}, -- -29,-39
        {map = "142087179", changeMap = "top", gather = true}, -- -28,-39
        {map = "142087178", changeMap = "right", gather = true}, -- -28,-40
        {map = "142087690", changeMap = "top", gather = true}, -- -27,-40
        {map = "142087689", changeMap = "right", gather = true}, -- -27,-41
        {map = "142088201", changeMap = "top", gather = true}, -- -26,-41
        {map = "142088712", changeMap = "top", gather = true}, -- -25,-42
        {map = "142088711", changeMap = "top", gather = true}, -- -25,-43
        {map = "142088710", changeMap = "top", gather = true}, -- -25,-44
        {map = "142088709", changeMap = "top", gather = true}, -- -25,-45
        {map = "142088708", changeMap = "top", gather = true}, -- -25,-46
        {map = "142088707", changeMap = "top", gather = true}, -- -25,-47
        {map = "159649809", changeMap = "top", gather = true}, -- -25,-48
        {map = "159649808", changeMap = "top", gather = true}, -- -25,-49
        {map = "159649807", changeMap = "top", gather = true}, -- -25,-50
        {map = "159649806", changeMap = "top", gather = true}, -- -25,-51
        {map = "159649805", changeMap = "top", gather = true}, -- -25,-52
        {map = "159649804", changeMap = "top", gather = true}, -- -25,-53
        {map = "159649803", changeMap = "right", gather = true}, -- -25,-54
        {map = "159650315", changeMap = "top", gather = true}, -- -24,-54
        {map = "159650314", changeMap = "top", gather = true}, -- -24,-55
        {map = "159650313", changeMap = "top", gather = true}, -- -24,-56
        {map = "159650312", changeMap = "top", gather = true}, -- -24,-57
        {map = "159650311", changeMap = "right", gather = true}, -- -24,-58
        {map = "158991364", changeMap = "bottom", gather = true}, -- -23,-58
        {map = "158991365", changeMap = "right", gather = true}, -- -23,-57
        {map = "158990853", changeMap = "right", gather = true}, -- -22,-57
        {map = "158859269", changeMap = "right", gather = true}, -- -21,-57
        {map = "158859781", changeMap = "right", gather = true}, -- -20,-57
        {map = "158860293", changeMap = "right", gather = true}, -- -19,-57
        {map = "158860805", changeMap = "right", gather = true}, -- -18,-57
        {map = "158861317", changeMap = "right", gather = true}, -- -17,-57
        {map = "158861829", changeMap = "right", gather = true}, -- -16,-57
        {map = "155975682", changeMap = "right", gather = true}, -- -15,-57
        {map = "155976194", changeMap = "right", gather = true}, -- -14,-57
        {map = "155976706", changeMap = "bottom", gather = true}, -- -13,-57
        {map = "155976707", changeMap = "bottom", gather = true}, -- -13,-56
        {map = "155976708", changeMap = "bottom", gather = true}, -- -13,-55
        {map = "155976709", changeMap = "left", gather = true}, -- -13,-54
        {map = "155976197", changeMap = "left", gather = true}, -- -14,-54
        {map = "155975685", changeMap = "bottom", gather = true}, -- -15,-54
        {map = "155975686", changeMap = "bottom", gather = true}, -- -15,-53
        {map = "155975687", changeMap = "right", gather = true}, -- -15,-52
        {map = "155976199", changeMap = "right", gather = true}, -- -14,-52
        {map = "155976711", changeMap = "right", gather = true}, -- -13,-52
        {map = "155977223", changeMap = "bottom", gather = true}, -- -12,-52
        {map = "155977224", changeMap = "bottom", gather = true}, -- -12,-51
        {map = "155977225", changeMap = "left", gather = true}, -- -12,-50
        {map = "155976713", changeMap = "left", gather = true}, -- -13,-50
        {map = "155976201", changeMap = "left", gather = true}, -- -14,-50
        {map = "155975689", changeMap = "bottom", gather = true}, -- -15,-50
        {map = "155975690", changeMap = "bottom", gather = true}, -- -15,-49
        {map = "155975691", changeMap = "bottom", gather = true}, -- -15,-48
        {map = "155975692", changeMap = "left", gather = true}, -- -15,-47
        {map = "147590665", changeMap = "left", gather = true}, -- -16,-47
        {map = "147590153", changeMap = "bottom", gather = true}, -- -17,-47
        {map = "147590154", changeMap = "left", gather = true}, -- -17,-46
        {map = "147589642", changeMap = "top", gather = true}, -- -18,-46
        {map = "147589641", changeMap = "left", gather = true}, -- -18,-47
        {map = "147589129", changeMap = "top", gather = true}, -- -19,-47
        {map = "147589128", changeMap = "top", gather = true}, -- -19,-48
        {map = "147589127", changeMap = "left", gather = true}, -- -19,-49
        {map = "147588615", changeMap = "top", gather = true}, -- -20,-49
        {map = "147588614", changeMap = "left", gather = true}, -- -20,-50
        {map = "147588102", changeMap = "left", gather = true}, -- -21,-50
        {map = "147587590", changeMap = "left", gather = true}, -- -22,-50
        {map = "147587078", changeMap = "bottom", gather = true}, -- -23,-50
        {map = "147587079", changeMap = "bottom", gather = true}, -- -23,-49
        {map = "147587080", changeMap = "right", gather = true}, -- -23,-48
        {map = "147587592", changeMap = "right", gather = true}, -- -22,-48
        {map = "147588104", changeMap = "bottom", gather = true}, -- -21,-48
        {map = "147588105", changeMap = "bottom", gather = true}, -- -21,-47
        {map = "147588106", changeMap = "right", gather = true}, -- -21,-46
        {map = "147588618", changeMap = "bottom", gather = true}, -- -20,-46
        {map = "147588619", changeMap = "bottom", gather = true}, -- -20,-45
        {map = "139461121", changeMap = "right", gather = true}, -- -20,-44
        {map = "139461633", changeMap = "right", gather = true}, -- -19,-44
        {map = "139462145", changeMap = "right", gather = true}, -- -18,-44
        {map = "139462657", changeMap = "right", gather = true}, -- -17,-44
        {map = "139463169", changeMap = "right", gather = true}, -- -16,-44
        {map = "139463681", changeMap = "bottom", gather = true}, -- -15,-44
        {map = "139463682", changeMap = "bottom", gather = true}, -- -15,-43
        {map = "139463683", changeMap = "left", gather = true}, -- -15,-42
        {map = "139463171", changeMap = "left", gather = true}, -- -16,-42
        {map = "139462659", changeMap = "left", gather = true}, -- -17,-42
        {map = "139462147", changeMap = "left", gather = true}, -- -18,-42
        {map = "139461635", changeMap = "bottom", gather = true}, -- -19,-42
        {map = "139461636", changeMap = "bottom", gather = true}, -- -19,-41
        {map = "139461637", changeMap = "bottom", gather = true}, -- -19,-40
        {map = "139461638", changeMap = "bottom", gather = true}, -- -19,-39
        {map = "139461639", changeMap = "left", gather = true}, -- -19,-38
        {map = "139461127", changeMap = "left", gather = true}, -- -20,-38
        {map = "142090764", changeMap = "left", gather = true}, -- -21,-38
        {map = "142090252", changeMap = "left", gather = true}, -- -22,-38
        {map = "142089740", changeMap = "left", gather = true}, -- -23,-38
        {map = "142089228", changeMap = "bottom", gather = true}, -- -24,-38
        {map = "142089229", changeMap = "bottom", gather = true}, -- -24,-37
        {map = "142089230", changeMap = "bottom", gather = true}, -- -24,-36
        {map = "142089231", changeMap = "left", gather = true}, -- -24,-35
        {map = "142088719", changeMap = "left", gather = true}, -- -25,-35
        {map = "142088207", changeMap = "left", gather = true}, -- -26,-35
    }
end

function bank()
    return {
        --HavreSac--
        {map = "162791424", changeMap = "usezaap:191105026", gather = true}, -- 
        --Astrub--
        {map = "191105026", changeMap = "goto:192415750", gather = true},
        {map = "192415750", custom = banquier, gather = true},
    } 
end
function banquier()
    global_Delay(1000)
    npc_Speak(-20000) -- [-20000] = id du npc banque Astrub
    global_Delay(1000)
    npc_Reply(64347) -- [64347] = id de la reponse
    global_Delay(1000)
    storage_DropAll() -- On vide TOUT les items
    global_Delay(1000)
    npc_Close() -- On ferme la banque
    global_Delay(1000)
    global_ChangeMap("goto:191104002") -- Sortir de la banque
end