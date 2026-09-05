var _loc1 = ank.battlefield.datacenter["\x13\n"].prototype;
_loc1.isTactic2 = function(_loc2_)
{
   var _loc2_ = false;
   if(this.layerGroundNum == 0 && (this.groundSlope == 1 && (this.layerObject2Num == 0 || (this.layerObject2Num == dofus["\x12\x03"].getTacticLayerObject2(_loc2_.subarea) || (this.layerObject2Num == 25 || this.layerObject2Num == 1030)))))
   {
      if(!this.lineOfSight)
      {
         if(this.layerObject1Num == dofus["\x12\x03"].getTacticGfx(_loc2_.subarea,0))
         {
            _loc2_ = true;
         }
      }
      else if(this.movement == 0 || this.movement == 1)
      {
         if(this.layerObject1Num == 10002)
         {
            _loc2_ = true;
         }
      }
      else if(this.layerObject1Num == dofus["\x12\x03"].getTacticGfx(_loc2_.subarea,1) || this.layerObject1Num == dofus["\x12\x03"].getTacticGfx(_loc2_.subarea,3))
      {
         _loc2_ = true;
      }
   }
   return _loc2_;
};
_loc1.turnTactic = function(_loc2_, _loc3_)
{
   var _loc4_ = this.isTrigger;
   if(this.nPermanentLevel == 0)
   {
      this.nPermanentLevel = 1;
   }
   this.layerGroundNum = 0;
   this.groundSlope = 1;
   this.layerObject1Rot = 0;
   if(!this.lineOfSight)
   {
      this.layerObject1Num = dofus["\x12\x03"].getTacticGfx(_loc3_.subarea,0);
   }
   else if(this.movement == 0 || this.movement == 1)
   {
      this.layerObject1Num = 10002;
   }
   else
   {
      var _loc2_ = ank.battlefield["\x1e\n\t"]["\x1e\x16\x1b"].getCaseCoordonnee(_loc2_,this.num);
      var _loc5_ = Math.abs(_loc2_.x) % 2 == Math.abs(_loc2_.y) % 2;
      this.layerObject1Num = !!_loc5_ ? dofus["\x12\x03"].getTacticGfx(_loc3_.subarea,1) : dofus["\x12\x03"].getTacticGfx(_loc3_.subarea,3);
   }
   if(this.layerObject2Num != 25)
   {
      if(!this.lineOfSight)
      {
         this.layerObject2Num = dofus["\x12\x03"].getTacticLayerObject2(_loc3_.subarea);
      }
      else if(_loc4_)
      {
         this.layerObject2Num = 1030;
      }
      else
      {
         this.layerObject2Num = 0;
      }
   }
};
